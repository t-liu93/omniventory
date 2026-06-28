"""M2 Step 3 tests: instance alterations, ledger wiring, and backfill.

Required coverage (per M2.md §9 Step 3 / §10 blind-review points):

Service / repository:
- Create per mode: exact, level, none (happy path and field validation).
- ``exact`` create records exactly one initial intake; quantity == SUM(deltas).
- ``level`` create requires stock_level, quantity NULL.
- ``none`` create stores neither, quantity NULL.
- Field/mode mismatch rejected (instance.field_mode_mismatch).
- Bad stock_level → validation.unsupported_stock_level.
- Serial⇒qty=1 under nullable quantity: serialized exact lot with qty 1 OK;
  bad qty rejected at service layer (422).
- recompute_quantity helper returns Decimal, and quantity == SUM(deltas) invariant.
- DB CHECK blocks a direct bad write (serial IS NULL OR quantity IS NULL OR quantity = 1).
- InstanceUpdate no longer accepts quantity (ignored / not in schema).
- stock_level updatable for level-mode lots; not for exact/none.

Migration 0012:
- upgrade adds stock_level + received_at columns.
- upgrade changes quantity from NOT NULL → nullable.
- upgrade rewrites the serial CHECK to the new expression.
- upgrade backfills one intake per existing lot (delta = old qty,
  occurred_at = received_at = created_at, user_id NULL).
- Displayed quantities unchanged after backfill.
- downgrade deletes the backfilled movements.
- downgrade restores quantity NOT NULL and old CHECK.
- downgrade drops stock_level and received_at.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_temp_db_url() -> tuple[str, Path]:
    """Return (url, path) for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m2step3_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()  # Start empty.
    return f"sqlite:///{path_str}", path


# ---------------------------------------------------------------------------
# Fixtures
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
    import importlib

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

    # Seed item_kinds.
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
) -> object:
    """Seed a definition with the given tracking mode."""
    from sqlalchemy import select

    from app.models.item_definition import ItemDefinition
    from app.models.item_kind import ItemKind

    kind = session.scalars(select(ItemKind).where(ItemKind.code == "consumable")).first()
    assert kind is not None
    defn = ItemDefinition(
        name=name or f"TestDef-{mode}",
        unit="pcs",
        kind_id=kind.id,
        stock_tracking_mode=mode,
    )
    session.add(defn)
    session.flush()
    return defn


# ---------------------------------------------------------------------------
# 1. Error code registration
# ---------------------------------------------------------------------------


class TestErrorCodeRegistration:
    """New error codes for Step 3 must be registered in ErrorCode."""

    def test_instance_field_mode_mismatch_registered(self) -> None:
        """ErrorCode.INSTANCE_FIELD_MODE_MISMATCH is defined."""
        from app.core.errors import ErrorCode

        assert hasattr(ErrorCode, "INSTANCE_FIELD_MODE_MISMATCH")
        assert ErrorCode.INSTANCE_FIELD_MODE_MISMATCH == "instance.field_mode_mismatch"

    def test_unsupported_stock_level_registered(self) -> None:
        """ErrorCode.UNSUPPORTED_STOCK_LEVEL is defined."""
        from app.core.errors import ErrorCode

        assert hasattr(ErrorCode, "UNSUPPORTED_STOCK_LEVEL")
        assert ErrorCode.UNSUPPORTED_STOCK_LEVEL == "validation.unsupported_stock_level"


# ---------------------------------------------------------------------------
# 2. Mode-aware create (service unit tests)
# ---------------------------------------------------------------------------


class TestModeAwareCreate:
    """StockInstanceService.create branches correctly on tracking mode."""

    # ── exact mode ──────────────────────────────────────────────────────────

    def test_exact_create_records_initial_intake(self, db_session: Session) -> None:
        """exact create → one intake movement in the ledger."""
        from sqlalchemy import select

        from app.models.stock_movement import StockMovement
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact")
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("5")))
        db_session.commit()

        movements = db_session.scalars(
            select(StockMovement).where(StockMovement.instance_id == inst.id)
        ).all()
        assert len(movements) == 1
        m = movements[0]
        assert m.type == "intake"
        assert m.quantity_delta == Decimal("5")

    def test_exact_create_quantity_equals_sum_of_deltas(self, db_session: Session) -> None:
        """exact create: quantity == SUM(quantity_delta) — the ledger invariant."""
        from app.repositories.stock_movement import StockMovementRepository
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact")
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("7.5")))
        db_session.commit()
        db_session.refresh(inst)

        movement_repo = StockMovementRepository(db_session)
        delta_sum = movement_repo.sum_delta_for_instance(inst.id)
        assert inst.quantity == delta_sum
        assert inst.quantity == Decimal("7.5")

    def test_exact_create_defaults_quantity_to_1(self, db_session: Session) -> None:
        """exact create without quantity → intake delta = 1, quantity = 1."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact")
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id))
        db_session.commit()
        db_session.refresh(inst)

        assert inst.quantity == Decimal("1")

    def test_exact_create_stock_level_rejected(self, db_session: Session) -> None:
        """exact create with stock_level raises instance.field_mode_mismatch 422."""
        from app.core.errors import AppError, ErrorCode
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact")
        svc = StockInstanceService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.create(InstanceCreate(definition_id=defn.id, stock_level="low"))
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == ErrorCode.INSTANCE_FIELD_MODE_MISMATCH
        assert exc_info.value.params is not None
        assert exc_info.value.params["mode"] == "exact"
        assert exc_info.value.params["field"] == "stock_level"

    # ── level mode ──────────────────────────────────────────────────────────

    def test_level_create_requires_stock_level(self, db_session: Session) -> None:
        """level create without stock_level raises instance.field_mode_mismatch 422."""
        from app.core.errors import AppError, ErrorCode
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="level")
        svc = StockInstanceService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.create(InstanceCreate(definition_id=defn.id))
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == ErrorCode.INSTANCE_FIELD_MODE_MISMATCH

    def test_level_create_stores_stock_level(self, db_session: Session) -> None:
        """level create → stores stock_level, quantity is NULL."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="level")
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, stock_level="high"))
        db_session.commit()
        db_session.refresh(inst)

        assert inst.stock_level == "high"
        assert inst.quantity is None

    def test_level_create_quantity_rejected(self, db_session: Session) -> None:
        """level create with quantity raises instance.field_mode_mismatch 422."""
        from app.core.errors import AppError, ErrorCode
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="level")
        svc = StockInstanceService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.create(
                InstanceCreate(definition_id=defn.id, quantity=Decimal("5"), stock_level="low")
            )
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == ErrorCode.INSTANCE_FIELD_MODE_MISMATCH
        assert exc_info.value.params is not None
        assert exc_info.value.params["field"] == "quantity"

    def test_level_create_no_movements(self, db_session: Session) -> None:
        """level create → no movements are created."""
        from sqlalchemy import select

        from app.models.stock_movement import StockMovement
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="level")
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, stock_level="medium"))
        db_session.commit()

        movements = db_session.scalars(
            select(StockMovement).where(StockMovement.instance_id == inst.id)
        ).all()
        assert movements == []

    def test_level_create_bad_stock_level_rejected(self, db_session: Session) -> None:
        """level create with bad stock_level → validation.unsupported_stock_level."""
        from app.core.errors import AppError, ErrorCode
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="level")
        svc = StockInstanceService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.create(InstanceCreate(definition_id=defn.id, stock_level="critical"))
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_STOCK_LEVEL
        params = exc_info.value.params or {}
        assert params.get("value") == "critical"
        assert "supported" in params
        assert set(params["supported"]) == {"high", "medium", "low"}

    @pytest.mark.parametrize("level", ["high", "medium", "low"])
    def test_level_all_valid_levels_accepted(self, db_session: Session, level: str) -> None:
        """level create with high/medium/low is accepted."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="level", name=f"Level-{level}")
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, stock_level=level))
        db_session.commit()
        db_session.refresh(inst)
        assert inst.stock_level == level

    # ── none mode ────────────────────────────────────────────────────────────

    def test_none_create_stores_neither(self, db_session: Session) -> None:
        """none create → quantity NULL, stock_level NULL."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="none")
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id))
        db_session.commit()
        db_session.refresh(inst)

        assert inst.quantity is None
        assert inst.stock_level is None

    def test_none_create_quantity_rejected(self, db_session: Session) -> None:
        """none create with quantity raises instance.field_mode_mismatch 422."""
        from app.core.errors import AppError, ErrorCode
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="none")
        svc = StockInstanceService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("1")))
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == ErrorCode.INSTANCE_FIELD_MODE_MISMATCH
        assert exc_info.value.params is not None
        assert exc_info.value.params["field"] == "quantity"

    def test_none_create_stock_level_rejected(self, db_session: Session) -> None:
        """none create with stock_level raises instance.field_mode_mismatch 422."""
        from app.core.errors import AppError, ErrorCode
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="none")
        svc = StockInstanceService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.create(InstanceCreate(definition_id=defn.id, stock_level="low"))
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == ErrorCode.INSTANCE_FIELD_MODE_MISMATCH

    def test_none_create_no_movements(self, db_session: Session) -> None:
        """none create → no movements."""
        from sqlalchemy import select

        from app.models.stock_movement import StockMovement
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="none")
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id))
        db_session.commit()

        movements = db_session.scalars(
            select(StockMovement).where(StockMovement.instance_id == inst.id)
        ).all()
        assert movements == []


# ---------------------------------------------------------------------------
# 3. Ledger invariant: quantity == SUM(deltas)
# ---------------------------------------------------------------------------


class TestLedgerInvariant:
    """quantity is never blind-set — always derived from SUM(quantity_delta)."""

    def test_quantity_never_blind_set(self, db_session: Session) -> None:
        """After create, quantity equals the sum of all movement deltas."""
        from sqlalchemy import func, select

        from app.models.stock_movement import StockMovement
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact")
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("10.5")))
        db_session.commit()
        db_session.refresh(inst)

        # Query the actual sum from the DB.
        db_sum = db_session.scalar(
            select(func.coalesce(func.sum(StockMovement.quantity_delta), 0)).where(
                StockMovement.instance_id == inst.id
            )
        )
        assert Decimal(str(db_sum)) == inst.quantity

    def test_recompute_quantity_helper(self, db_session: Session) -> None:
        """recompute_quantity returns SUM(deltas) as Decimal."""
        from app.repositories.stock_movement import StockMovementRepository
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact")
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("3")))
        db_session.commit()
        db_session.refresh(inst)

        # The service's recompute_quantity must match the repo's sum_delta.
        movement_repo = StockMovementRepository(db_session)
        expected = movement_repo.sum_delta_for_instance(inst.id)
        # Verify with a fresh recompute call.
        recomputed = svc.recompute_quantity(inst)
        db_session.flush()
        assert recomputed == expected
        assert isinstance(recomputed, Decimal)


# ---------------------------------------------------------------------------
# 4. serial ⇒ qty=1 under nullable quantity
# ---------------------------------------------------------------------------


class TestSerialQtyOneUnderNullableQuantity:
    """serial ⇒ qty=1 still enforced after nullable-quantity change."""

    def test_serialized_exact_create_qty_1_ok(self, db_session: Session) -> None:
        """exact create with serial + quantity=1 is accepted."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact")
        svc = StockInstanceService(db_session)
        inst = svc.create(
            InstanceCreate(definition_id=defn.id, serial="SN-001", quantity=Decimal("1"))
        )
        db_session.commit()
        db_session.refresh(inst)

        assert inst.serial == "SN-001"
        assert inst.quantity == Decimal("1")

    def test_serialized_exact_create_qty_gt_1_rejected(self, db_session: Session) -> None:
        """exact create with serial + quantity > 1 rejected at service (422)."""
        from app.core.errors import AppError, ErrorCode
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact")
        svc = StockInstanceService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.create(
                InstanceCreate(definition_id=defn.id, serial="SN-002", quantity=Decimal("2"))
            )
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == ErrorCode.STOCK_INSTANCE_SERIAL_REQUIRES_QTY_ONE

    def test_db_check_allows_null_quantity_with_serial(self, db_session: Session) -> None:
        """DB CHECK allows (serial IS NOT NULL, quantity IS NULL) — for non-exact lots."""
        from sqlalchemy import select

        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.stock_instance import StockInstance

        kind = db_session.scalars(select(ItemKind).where(ItemKind.code == "durable")).first()
        assert kind is not None
        defn = ItemDefinition(
            name="NoneItem", unit="pcs", kind_id=kind.id, stock_tracking_mode="none"
        )
        db_session.add(defn)
        db_session.flush()

        # Direct write: serial set, quantity NULL — DB CHECK (serial IS NULL OR quantity IS NULL OR qty=1)
        # must pass because quantity IS NULL.
        good = StockInstance(
            definition_id=defn.id,
            serial="SN-LEVEL",
            quantity=None,  # NULL is explicitly allowed by the new CHECK
        )
        db_session.add(good)
        db_session.flush()  # must not raise
        db_session.rollback()

    def test_db_check_rejects_serial_and_qty_gt_1(self, db_session: Session) -> None:
        """DB CHECK rejects (serial IS NOT NULL, quantity > 1) — backstop after service guard."""
        from sqlalchemy import select

        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.stock_instance import StockInstance

        kind = db_session.scalars(select(ItemKind).where(ItemKind.code == "durable")).first()
        assert kind is not None
        defn = ItemDefinition(name="BadCheckItem", unit="pcs", kind_id=kind.id)
        db_session.add(defn)
        db_session.flush()

        bad = StockInstance(
            definition_id=defn.id,
            serial="SN-BAD",
            quantity=Decimal("3"),
        )
        db_session.add(bad)
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_db_check_allows_qty_1_with_serial(self, db_session: Session) -> None:
        """DB CHECK allows (serial IS NOT NULL, quantity = 1) — the valid serial case."""
        from sqlalchemy import select

        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.stock_instance import StockInstance

        kind = db_session.scalars(select(ItemKind).where(ItemKind.code == "durable")).first()
        assert kind is not None
        defn = ItemDefinition(name="SerialQty1", unit="pcs", kind_id=kind.id)
        db_session.add(defn)
        db_session.flush()

        good = StockInstance(
            definition_id=defn.id,
            serial="SN-ONE",
            quantity=Decimal("1"),
        )
        db_session.add(good)
        db_session.flush()  # must not raise
        db_session.rollback()


# ---------------------------------------------------------------------------
# 5. InstanceUpdate no longer has quantity
# ---------------------------------------------------------------------------


class TestInstanceUpdateNoQuantity:
    """InstanceUpdate schema must not include quantity (M2 §2)."""

    def test_instance_update_has_no_quantity_field(self) -> None:
        """InstanceUpdate.model_fields does not contain 'quantity'."""
        from app.schemas.stock_instance import InstanceUpdate

        assert "quantity" not in InstanceUpdate.model_fields

    def test_instance_update_has_stock_level(self) -> None:
        """InstanceUpdate has 'stock_level' field."""
        from app.schemas.stock_instance import InstanceUpdate

        assert "stock_level" in InstanceUpdate.model_fields

    def test_instance_response_quantity_is_nullable(self) -> None:
        """InstanceResponse.quantity is Optional[Decimal]."""
        from app.schemas.stock_instance import InstanceResponse

        # Pydantic V2: check the annotation
        field = InstanceResponse.model_fields["quantity"]
        assert field.is_required() is False or "None" in str(field.annotation)

    def test_instance_response_has_stock_level_and_received_at(self) -> None:
        """InstanceResponse has stock_level and received_at fields."""
        from app.schemas.stock_instance import InstanceResponse

        assert "stock_level" in InstanceResponse.model_fields
        assert "received_at" in InstanceResponse.model_fields


# ---------------------------------------------------------------------------
# 6. stock_level update (mode-aware)
# ---------------------------------------------------------------------------


class TestStockLevelUpdate:
    """stock_level is updatable for level-mode lots; not for exact/none."""

    def test_level_lot_can_update_stock_level(self, db_session: Session) -> None:
        """PATCH: level lot can change stock_level."""
        from app.schemas.stock_instance import InstanceCreate, InstanceUpdate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="level")
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, stock_level="high"))
        db_session.commit()

        updated = svc.update(inst.id, InstanceUpdate(stock_level="low"))
        db_session.commit()
        db_session.refresh(updated)
        assert updated.stock_level == "low"

    def test_exact_lot_cannot_update_to_stock_level(self, db_session: Session) -> None:
        """PATCH: exact lot cannot set stock_level."""
        from app.core.errors import AppError, ErrorCode
        from app.schemas.stock_instance import InstanceCreate, InstanceUpdate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact")
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("3")))
        db_session.commit()

        with pytest.raises(AppError) as exc_info:
            svc.update(inst.id, InstanceUpdate(stock_level="low"))
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == ErrorCode.INSTANCE_FIELD_MODE_MISMATCH

    def test_level_lot_bad_stock_level_rejected_on_update(self, db_session: Session) -> None:
        """PATCH: level lot with bad stock_level → validation.unsupported_stock_level."""
        from app.core.errors import AppError, ErrorCode
        from app.schemas.stock_instance import InstanceCreate, InstanceUpdate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="level")
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, stock_level="high"))
        db_session.commit()

        with pytest.raises(AppError) as exc_info:
            svc.update(inst.id, InstanceUpdate(stock_level="critical"))
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_STOCK_LEVEL


# ---------------------------------------------------------------------------
# 7. HTTP API — end-to-end mode-aware create/update
# ---------------------------------------------------------------------------


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite DB for HTTP-level tests."""
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-m2-step3")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def test_client(temp_db: Path) -> Generator[object]:  # noqa: ARG001
    """TestClient with full schema and authenticated admin session."""
    import importlib

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


def _create_definition_http(client: object, name: str, **kwargs: object) -> dict:  # type: ignore[type-arg]
    from fastapi.testclient import TestClient

    assert isinstance(client, TestClient)
    payload = {"name": name, **kwargs}
    resp = client.post("/api/definitions", json=payload)
    assert resp.status_code == 201, f"create_definition failed: {resp.text}"
    return resp.json()  # type: ignore[return-value]


def _create_instance_http(
    client: object,
    definition_id: int,
    *,
    expect_status: int = 201,
    **kwargs: object,
) -> dict:  # type: ignore[type-arg]
    from fastapi.testclient import TestClient

    assert isinstance(client, TestClient)
    payload = {"definition_id": definition_id, **kwargs}
    resp = client.post("/api/instances", json=payload)
    assert resp.status_code == expect_status, (
        f"create_instance failed: {resp.status_code} {resp.text}"
    )
    return resp.json()  # type: ignore[return-value]


class TestHTTPModeAwareCreate:
    """HTTP-level mode-aware create tests."""

    def test_exact_create_returns_quantity(self, test_client: object) -> None:
        """POST /instances for exact mode returns a non-null quantity."""
        defn = _create_definition_http(test_client, "Nails", stock_tracking_mode="exact")
        data = _create_instance_http(test_client, defn["id"], quantity="10")
        assert data["quantity"] is not None
        assert Decimal(data["quantity"]) == Decimal("10")

    def test_exact_create_response_has_stock_level_null(self, test_client: object) -> None:
        """POST /instances for exact mode: stock_level is null in response."""
        defn = _create_definition_http(test_client, "Bolts", stock_tracking_mode="exact")
        data = _create_instance_http(test_client, defn["id"])
        assert data["stock_level"] is None

    def test_exact_create_response_has_received_at(self, test_client: object) -> None:
        """POST /instances for exact mode: received_at is set in response."""
        defn = _create_definition_http(test_client, "Screws", stock_tracking_mode="exact")
        data = _create_instance_http(test_client, defn["id"])
        assert data["received_at"] is not None

    def test_level_create_returns_null_quantity(self, test_client: object) -> None:
        """POST /instances for level mode: quantity is null."""
        defn = _create_definition_http(test_client, "Assorted Screws", stock_tracking_mode="level")
        data = _create_instance_http(test_client, defn["id"], stock_level="low")
        assert data["quantity"] is None
        assert data["stock_level"] == "low"

    def test_none_create_returns_both_null(self, test_client: object) -> None:
        """POST /instances for none mode: quantity and stock_level are null."""
        defn = _create_definition_http(test_client, "Wall Art", stock_tracking_mode="none")
        data = _create_instance_http(test_client, defn["id"])
        assert data["quantity"] is None
        assert data["stock_level"] is None

    def test_exact_create_mode_mismatch_422(self, test_client: object) -> None:
        """POST /instances for exact mode with stock_level → 422."""
        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        defn = _create_definition_http(test_client, "Item", stock_tracking_mode="exact")
        resp = test_client.post(
            "/api/instances",
            json={"definition_id": defn["id"], "stock_level": "low"},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "instance.field_mode_mismatch"

    def test_level_create_missing_stock_level_422(self, test_client: object) -> None:
        """POST /instances for level mode without stock_level → 422."""
        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        defn = _create_definition_http(test_client, "Screws2", stock_tracking_mode="level")
        resp = test_client.post(
            "/api/instances",
            json={"definition_id": defn["id"]},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "instance.field_mode_mismatch"

    def test_level_update_stock_level(self, test_client: object) -> None:
        """PATCH /instances/{id}: level mode allows stock_level update."""
        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        defn = _create_definition_http(test_client, "Assorted", stock_tracking_mode="level")
        inst = _create_instance_http(test_client, defn["id"], stock_level="high")
        resp = test_client.patch(
            f"/api/instances/{inst['id']}",
            json={"stock_level": "low"},
        )
        assert resp.status_code == 200
        assert resp.json()["stock_level"] == "low"

    def test_patch_quantity_silently_ignored(self, test_client: object) -> None:
        """PATCH /instances/{id} with 'quantity' in body — silently ignored (M2 contract)."""
        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        defn = _create_definition_http(test_client, "Nails2", stock_tracking_mode="exact")
        inst = _create_instance_http(test_client, defn["id"], quantity="5")
        original_qty = inst["quantity"]

        resp = test_client.patch(
            f"/api/instances/{inst['id']}",
            json={"quantity": "999"},  # ignored by InstanceUpdate schema
        )
        assert resp.status_code == 200
        # Quantity unchanged (not in schema, so extra key is ignored by Pydantic)
        assert resp.json()["quantity"] == original_qty


# ---------------------------------------------------------------------------
# 8. Alembic migration 0012
# ---------------------------------------------------------------------------


class TestAlembicMigration0012:
    """Migration 0012 must upgrade and downgrade cleanly, including backfill."""

    def _run_alembic(self, *args: str, url: str) -> tuple[int, str]:
        """Run alembic as a subprocess; return (returncode, output)."""
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

    def test_upgrade_0012_adds_columns(self) -> None:
        """Upgrading to 0012 adds stock_level and received_at to stock_instances."""
        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "0012", url=url)
            assert rc == 0, f"alembic upgrade 0012 failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols = conn.execute(text("PRAGMA table_info(stock_instances)")).fetchall()
                col_names = {row[1] for row in cols}
                assert "stock_level" in col_names, f"stock_level missing; cols: {col_names}"
                assert "received_at" in col_names, f"received_at missing; cols: {col_names}"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0012_quantity_nullable(self) -> None:
        """After upgrade 0012, quantity allows NULL values."""
        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "0011", url=url)
            assert rc == 0, f"upgrade 0011 failed:\n{out}"

            # Pre-insert an instance with quantity.
            engine = create_engine(url)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO item_definitions (name, kind_id, unit, stock_tracking_mode) "
                        "VALUES ('TestDef', 1, 'pcs', 'exact')"
                    )
                )
                conn.execute(
                    text("INSERT INTO stock_instances (definition_id, quantity) VALUES (1, 5)")
                )

            rc2, out2 = self._run_alembic("upgrade", "0012", url=url)
            assert rc2 == 0, f"upgrade 0012 failed:\n{out2}"

            # Now try to insert a row with NULL quantity.
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO stock_instances (definition_id, quantity, stock_level) "
                        "VALUES (1, NULL, 'low')"
                    )
                )
            # No exception = nullable works.
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0012_new_serial_check_allows_null_quantity(self) -> None:
        """After upgrade 0012, new CHECK allows (serial IS NOT NULL, quantity IS NULL)."""
        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "0012", url=url)
            assert rc == 0, f"upgrade 0012 failed:\n{out}"

            engine = create_engine(url)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO item_definitions (name, kind_id, unit, stock_tracking_mode) "
                        "VALUES ('TestDef2', 1, 'pcs', 'none')"
                    )
                )
                # serial set, quantity NULL — the new CHECK must allow this.
                conn.execute(
                    text(
                        "INSERT INTO stock_instances (definition_id, serial, quantity) "
                        "VALUES (1, 'SN-LEVEL', NULL)"
                    )
                )
            # No exception = new CHECK allows it.
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0012_new_serial_check_still_blocks_bad_write(self) -> None:
        """After upgrade 0012, new CHECK still blocks (serial IS NOT NULL, quantity > 1)."""

        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "0012", url=url)
            assert rc == 0, f"upgrade 0012 failed:\n{out}"

            engine = create_engine(url)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO item_definitions (name, kind_id, unit, stock_tracking_mode) "
                        "VALUES ('BadCheckDef', 1, 'pcs', 'exact')"
                    )
                )
            with pytest.raises((IntegrityError, Exception)), engine.begin() as conn:  # noqa: B017
                conn.execute(
                    text(
                        "INSERT INTO stock_instances (definition_id, serial, quantity) "
                        "VALUES (1, 'SN-BAD', 5)"
                    )
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0012_backfills_intake_movements(self) -> None:
        """Upgrade 0012 backfills exactly one intake movement per pre-existing lot."""
        url, db_path = _make_temp_db_url()
        try:
            # Upgrade to 0011 (just before 0012).
            rc11, out11 = self._run_alembic("upgrade", "0011", url=url)
            assert rc11 == 0, f"upgrade 0011 failed:\n{out11}"

            engine = create_engine(url)
            # Insert two lots with different quantities.
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO item_definitions (name, kind_id, unit, stock_tracking_mode) "
                        "VALUES ('Item A', 1, 'pcs', 'exact'), ('Item B', 1, 'pcs', 'exact')"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO stock_instances (definition_id, quantity) "
                        "VALUES (1, 10), (2, 3)"
                    )
                )

            # Apply 0012.
            rc12, out12 = self._run_alembic("upgrade", "0012", url=url)
            assert rc12 == 0, f"upgrade 0012 failed:\n{out12}"

            with engine.connect() as conn:
                movements = conn.execute(
                    text(
                        "SELECT instance_id, type, quantity_delta, user_id FROM stock_movements ORDER BY instance_id"
                    )
                ).fetchall()

            assert len(movements) == 2, f"Expected 2 backfilled movements, got {len(movements)}"
            # First lot: delta = 10
            assert movements[0][0] == 1  # instance_id
            assert movements[0][1] == "intake"
            assert Decimal(str(movements[0][2])) == Decimal("10")
            assert movements[0][3] is None  # user_id = NULL (system)
            # Second lot: delta = 3
            assert movements[1][0] == 2
            assert movements[1][1] == "intake"
            assert Decimal(str(movements[1][2])) == Decimal("3")
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0012_backfill_displayed_quantity_unchanged(self) -> None:
        """After 0012 upgrade, the displayed quantity (from stock_instances) is unchanged."""
        url, db_path = _make_temp_db_url()
        try:
            rc11, out11 = self._run_alembic("upgrade", "0011", url=url)
            assert rc11 == 0

            engine = create_engine(url)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO item_definitions (name, kind_id, unit, stock_tracking_mode) "
                        "VALUES ('Item', 1, 'pcs', 'exact')"
                    )
                )
                conn.execute(
                    text("INSERT INTO stock_instances (definition_id, quantity) VALUES (1, 7)")
                )

            rc12, out12 = self._run_alembic("upgrade", "0012", url=url)
            assert rc12 == 0, f"upgrade 0012 failed:\n{out12}"

            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT quantity FROM stock_instances WHERE id = 1")
                ).fetchone()
            assert row is not None
            assert Decimal(str(row[0])) == Decimal("7"), (
                f"Displayed quantity changed after backfill: {row[0]}"
            )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0012_backfill_received_at_equals_created_at(self) -> None:
        """After 0012 upgrade, received_at == created_at for pre-existing lots."""
        url, db_path = _make_temp_db_url()
        try:
            rc11, out11 = self._run_alembic("upgrade", "0011", url=url)
            assert rc11 == 0

            engine = create_engine(url)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO item_definitions (name, kind_id, unit, stock_tracking_mode) "
                        "VALUES ('Item', 1, 'pcs', 'exact')"
                    )
                )
                conn.execute(
                    text("INSERT INTO stock_instances (definition_id, quantity) VALUES (1, 5)")
                )

            rc12, out12 = self._run_alembic("upgrade", "0012", url=url)
            assert rc12 == 0, f"upgrade 0012 failed:\n{out12}"

            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT received_at, created_at FROM stock_instances WHERE id = 1")
                ).fetchone()
            assert row is not None
            # Both timestamps should be set (not None).
            assert row[0] is not None, "received_at should be set after backfill"
            assert row[1] is not None, "created_at should be set"
            # They should be equal (backfill sets received_at = created_at).
            assert row[0] == row[1], (
                f"received_at ({row[0]}) != created_at ({row[1]}) after backfill"
            )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_0012_deletes_backfilled_movements(self) -> None:
        """Downgrade from 0012 to 0011 deletes the backfilled intake movements."""
        url, db_path = _make_temp_db_url()
        try:
            rc11, out11 = self._run_alembic("upgrade", "0011", url=url)
            assert rc11 == 0

            engine = create_engine(url)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO item_definitions (name, kind_id, unit, stock_tracking_mode) "
                        "VALUES ('Item', 1, 'pcs', 'exact')"
                    )
                )
                conn.execute(
                    text("INSERT INTO stock_instances (definition_id, quantity) VALUES (1, 8)")
                )

            rc12, out12 = self._run_alembic("upgrade", "0012", url=url)
            assert rc12 == 0, f"upgrade 0012 failed:\n{out12}"

            # Verify the backfilled movement exists before downgrade.
            with engine.connect() as conn:
                count_before = conn.execute(text("SELECT COUNT(*) FROM stock_movements")).scalar()
            assert count_before == 1, f"Expected 1 backfilled movement, got {count_before}"

            # Downgrade.
            rc_dn, out_dn = self._run_alembic("downgrade", "0011", url=url)
            assert rc_dn == 0, f"downgrade to 0011 failed:\n{out_dn}"

            # The stock_movements table still exists (added in 0011); its rows should be gone.
            with engine.connect() as conn:
                count_after = conn.execute(text("SELECT COUNT(*) FROM stock_movements")).scalar()
            assert count_after == 0, (
                f"Backfilled movement should be deleted on downgrade, got {count_after}"
            )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_0012_restores_quantity_not_null(self) -> None:
        """Downgrade from 0012 restores quantity to NOT NULL."""
        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "0012", url=url)
            assert rc_up == 0, f"upgrade 0012 failed:\n{out_up}"

            rc_dn, out_dn = self._run_alembic("downgrade", "0011", url=url)
            assert rc_dn == 0, f"downgrade to 0011 failed:\n{out_dn}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols = conn.execute(text("PRAGMA table_info(stock_instances)")).fetchall()
                qty_col = next((c for c in cols if c[1] == "quantity"), None)
                assert qty_col is not None, "quantity column missing after downgrade"
                # SQLite PRAGMA: column[3] is 'notnull' (1 = NOT NULL).
                assert qty_col[3] == 1, (
                    f"quantity should be NOT NULL after downgrade, but notnull={qty_col[3]}"
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_0012_drops_new_columns(self) -> None:
        """Downgrade from 0012 removes stock_level and received_at."""
        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "0012", url=url)
            assert rc_up == 0, f"upgrade 0012 failed:\n{out_up}"

            rc_dn, out_dn = self._run_alembic("downgrade", "0011", url=url)
            assert rc_dn == 0, f"downgrade to 0011 failed:\n{out_dn}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols = conn.execute(text("PRAGMA table_info(stock_instances)")).fetchall()
                col_names = {row[1] for row in cols}
                assert "stock_level" not in col_names, "stock_level still present after downgrade"
                assert "received_at" not in col_names, "received_at still present after downgrade"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_full_upgrade_downgrade_roundtrip(self) -> None:
        """Full upgrade to head then downgrade to base leaves no application tables."""
        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "head", url=url)
            assert rc_up == 0, f"upgrade head failed:\n{out_up}"

            rc_dn, out_dn = self._run_alembic("downgrade", "base", url=url)
            assert rc_dn == 0, f"downgrade base failed:\n{out_dn}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'alembic_%' AND name != 'sqlite_sequence'"
                    )
                ).fetchall()
                assert tables == [], f"Tables still exist after full downgrade: {tables}"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_stepwise_upgrade_0011_to_0012(self) -> None:
        """Stepwise upgrade from 0011 to 0012 succeeds."""
        url, db_path = _make_temp_db_url()
        try:
            rc11, out11 = self._run_alembic("upgrade", "0011", url=url)
            assert rc11 == 0, f"upgrade 0011 failed:\n{out11}"

            rc12, out12 = self._run_alembic("upgrade", "0012", url=url)
            assert rc12 == 0, f"upgrade 0012 failed:\n{out12}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols = conn.execute(text("PRAGMA table_info(stock_instances)")).fetchall()
                col_names = {row[1] for row in cols}
                assert "stock_level" in col_names
                assert "received_at" in col_names
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_0012_with_null_quantity_lots_succeeds(self) -> None:
        """Downgrade from 0012 to 0011 succeeds even when level/none lots (quantity IS NULL)
        exist; those rows are removed, while exact lots survive.

        Covers Finding 1 from the blind review: batch-revert of quantity to NOT NULL
        would raise IntegrityError if NULL-quantity rows remain.
        """
        url, db_path = _make_temp_db_url()
        try:
            # Upgrade to 0012.
            rc_up, out_up = self._run_alembic("upgrade", "0012", url=url)
            assert rc_up == 0, f"upgrade 0012 failed:\n{out_up}"

            engine = create_engine(url)
            with engine.begin() as conn:
                # Insert an item definition.
                conn.execute(
                    text(
                        "INSERT INTO item_definitions (name, kind_id, unit, stock_tracking_mode) "
                        "VALUES ('ExactItem', 1, 'pcs', 'exact'), ('LevelItem', 1, 'pcs', 'level')"
                    )
                )
                # Insert one exact lot (quantity NOT NULL).
                conn.execute(
                    text("INSERT INTO stock_instances (definition_id, quantity) VALUES (1, 5)")
                )
                # Insert one level lot (quantity IS NULL) — created under 0012 feature.
                conn.execute(
                    text(
                        "INSERT INTO stock_instances (definition_id, quantity, stock_level) "
                        "VALUES (2, NULL, 'low')"
                    )
                )

            # Verify two rows exist before downgrade.
            with engine.connect() as conn:
                total = conn.execute(text("SELECT COUNT(*) FROM stock_instances")).scalar()
            assert total == 2, f"Expected 2 rows before downgrade, got {total}"

            # Downgrade to 0011 — must not raise IntegrityError.
            rc_dn, out_dn = self._run_alembic("downgrade", "0011", url=url)
            assert rc_dn == 0, (
                f"downgrade to 0011 failed (IntegrityError expected to be fixed):\n{out_dn}"
            )

            # After downgrade: the NULL-quantity (level) lot is removed; the exact lot survives.
            with engine.connect() as conn:
                remaining = conn.execute(
                    text("SELECT id, quantity FROM stock_instances ORDER BY id")
                ).fetchall()
            assert len(remaining) == 1, (
                f"Expected 1 row after downgrade (exact lot only), got {len(remaining)}: {remaining}"
            )
            assert remaining[0][1] is not None, "Surviving exact lot should have non-NULL quantity"
            assert Decimal(str(remaining[0][1])) == Decimal("5"), (
                f"Surviving exact lot quantity should be 5, got {remaining[0][1]}"
            )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_0012_preserves_operational_intake_with_user_id(self) -> None:
        """Downgrade from 0012 must NOT delete intake movements that carry a real user_id.

        Backfill movements are identified by user_id IS NULL; operational/create-time
        intakes (user_id IS NOT NULL) must survive the downgrade DELETE predicate.

        Covers Finding 2 from the blind review: the original predicate
        (type='intake' AND reverses_movement_id IS NULL) was too broad and would
        have deleted operational intakes in Steps 4+.
        """
        url, db_path = _make_temp_db_url()
        try:
            # Upgrade to 0011 first so we can pre-insert an exact lot.
            rc11, out11 = self._run_alembic("upgrade", "0011", url=url)
            assert rc11 == 0, f"upgrade 0011 failed:\n{out11}"

            engine = create_engine(url)
            with engine.begin() as conn:
                # Seed a user (needed for the FK on stock_movements.user_id).
                conn.execute(
                    text(
                        "INSERT INTO users (email, password_hash) "
                        "VALUES ('admin@example.com', 'hash')"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO item_definitions (name, kind_id, unit, stock_tracking_mode) "
                        "VALUES ('Item', 1, 'pcs', 'exact')"
                    )
                )
                conn.execute(
                    text("INSERT INTO stock_instances (definition_id, quantity) VALUES (1, 10)")
                )

            # Upgrade to 0012 (backfills one NULL-user intake for the pre-existing lot).
            rc12, out12 = self._run_alembic("upgrade", "0012", url=url)
            assert rc12 == 0, f"upgrade 0012 failed:\n{out12}"

            # Simulate an operational intake (user_id IS NOT NULL) that would exist in Step 4+.
            # We insert it directly to simulate a future step's service-layer create.
            with engine.begin() as conn:
                # user_id = 1 (the admin seeded above).
                conn.execute(
                    text(
                        "INSERT INTO stock_movements "
                        "(instance_id, type, quantity_delta, occurred_at, user_id, created_at) "
                        "VALUES (1, 'intake', 5, CURRENT_TIMESTAMP, 1, CURRENT_TIMESTAMP)"
                    )
                )

            # Verify: 2 movements exist — 1 backfill (user_id NULL) + 1 operational (user_id 1).
            with engine.connect() as conn:
                total_before = conn.execute(text("SELECT COUNT(*) FROM stock_movements")).scalar()
            assert total_before == 2, f"Expected 2 movements before downgrade, got {total_before}"

            # Downgrade to 0011.
            rc_dn, out_dn = self._run_alembic("downgrade", "0011", url=url)
            assert rc_dn == 0, f"downgrade to 0011 failed:\n{out_dn}"

            # After downgrade: only the backfill movement (user_id IS NULL) is deleted.
            # The operational intake (user_id = 1) must survive.
            with engine.connect() as conn:
                remaining_movements = conn.execute(
                    text("SELECT type, user_id FROM stock_movements")
                ).fetchall()
            assert len(remaining_movements) == 1, (
                f"Expected 1 movement after downgrade (operational intake), "
                f"got {len(remaining_movements)}: {remaining_movements}"
            )
            assert remaining_movements[0][0] == "intake", (
                "Surviving movement should be of type 'intake'"
            )
            assert remaining_movements[0][1] is not None, (
                "Surviving movement should have a non-NULL user_id (operational)"
            )
        finally:
            if db_path.exists():
                db_path.unlink()
