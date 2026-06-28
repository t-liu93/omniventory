"""M2 Step 4 tests: stock movement operations, FIFO consume, and reversal.

Required coverage (per M2.md §9 Step 4 / §10 blind-review points):

Service unit tests (StockMovementService):
- FIFO consume:
    - Single lot consumed fully.
    - Spanning multiple lots, oldest received_at first (proves FIFO ordering,
      not insertion order).
    - Partial-lot consumption uses exact Decimal precision.
    - Insufficient total stock rejected (stock.insufficient) with NOTHING written.
    - level/none definitions rejected (stock.movement_not_applicable).
- Intake / discard / adjust:
    - intake adds to quantity; quantity == SUM(deltas) after.
    - discard subtracts; quantity == SUM(deltas) after.
    - discard below 0 rejected (stock.negative_quantity).
    - adjust drives to absolute counted value (correct signed delta).
    - adjust with counted_quantity < 0 rejected (stock.negative_quantity).
    - Non-positive input (intake/discard quantity <= 0) rejected.
- Move:
    - Whole-lot location change with from/to recorded, delta = 0, qty unchanged.
    - Non-existent to_location_id → 404.
- Reverse / undo:
    - Reversing a consume restores quantity (the 🟢 path).
    - Double-reverse (same movement twice) → stock.movement_already_reversed.
    - Reverse-of-a-reversal → stock.cannot_reverse_reversal.
    - Reversal that would go negative → stock.reverse_would_go_negative.
    - Reversing a move restores location_id.
- Serial⇒qty=1 via intake: intake that pushes a serialized lot above 1 rejected.
- Ledger invariant: quantity == SUM(quantity_delta) after every operation sequence.
- Transaction integrity: a rejected op leaves zero side-effects.

Mode-change guard (ItemDefinitionService):
- Changing mode on a populated definition → tracking_mode_change_conflict (409).
- Changing mode on an empty definition → succeeds.

HTTP API (end-to-end):
- Each operation endpoint returns the updated InstanceResponse.
- GET /instances/{id}/movements returns the history (newest-first).
- POST /movements/{id}/reverse returns the updated InstanceResponse.
- POST /definitions/{id}/consume returns the list of touched InstanceResponses.
- Error codes are emitted via the M1.5 uniform envelope.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_temp_db_url() -> tuple[str, Path]:
    """Return (url, path) for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m2step4_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()
    return f"sqlite:///{path_str}", path


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


def _seed_user(session: Session) -> object:
    """Seed a minimal user and return it."""
    from app.models.user import User

    u = User(email="test@example.com", password_hash="hash")
    session.add(u)
    session.flush()
    return u


def _seed_definition(
    session: Session,
    *,
    mode: str = "exact",
    name: str | None = None,
) -> object:
    """Seed an ItemDefinition with the given tracking mode."""
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


def _seed_location(session: Session, name: str = "Shelf A") -> object:
    """Seed a minimal location and return it."""
    from app.models.location import Location

    loc = Location(name=name)
    session.add(loc)
    session.flush()
    return loc


def _seed_exact_lot(
    session: Session,
    definition_id: int,
    quantity: Decimal,
    *,
    received_at: datetime | None = None,
    serial: str | None = None,
    location_id: int | None = None,
) -> object:
    """Seed a stock instance with the given initial quantity via the service."""
    from app.models.stock_instance import StockInstance
    from app.repositories.stock_movement import StockMovementRepository

    # Create the instance row directly, then append the intake movement
    # (mirrors what StockInstanceService.create does for exact mode).
    inst = StockInstance(
        definition_id=definition_id,
        quantity=None,
        location_id=location_id,
        serial=serial,
    )
    if received_at is not None:
        inst.received_at = received_at
    session.add(inst)
    session.flush()

    repo = StockMovementRepository(session)
    repo.append(
        instance_id=inst.id,
        type="intake",
        quantity_delta=quantity,
        to_location_id=location_id,
    )
    from app.services.stock_instance import StockInstanceService

    svc = StockInstanceService(session)
    svc.recompute_quantity(inst)
    session.flush()
    return inst


def _make_ctx(session: Session) -> object:
    """Build a minimal RequestContext with a seeded user."""
    from app.core.context import RequestContext
    from app.models.household import Household

    user = _seed_user(session)
    hh = Household(name="Test HH")
    session.add(hh)
    session.flush()
    return RequestContext(household=hh, user=user)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Error code registration
# ---------------------------------------------------------------------------


class TestErrorCodeRegistration:
    """All Step 4 error codes must be registered in ErrorCode."""

    def test_stock_insufficient(self) -> None:
        from app.core.errors import ErrorCode

        assert ErrorCode.STOCK_INSUFFICIENT == "stock.insufficient"

    def test_stock_negative_quantity(self) -> None:
        from app.core.errors import ErrorCode

        assert ErrorCode.STOCK_NEGATIVE_QUANTITY == "stock.negative_quantity"

    def test_stock_movement_not_applicable(self) -> None:
        from app.core.errors import ErrorCode

        assert ErrorCode.STOCK_MOVEMENT_NOT_APPLICABLE == "stock.movement_not_applicable"

    def test_stock_movement_not_found(self) -> None:
        from app.core.errors import ErrorCode

        assert ErrorCode.STOCK_MOVEMENT_NOT_FOUND == "stock.movement_not_found"

    def test_stock_movement_already_reversed(self) -> None:
        from app.core.errors import ErrorCode

        assert ErrorCode.STOCK_MOVEMENT_ALREADY_REVERSED == "stock.movement_already_reversed"

    def test_stock_cannot_reverse_reversal(self) -> None:
        from app.core.errors import ErrorCode

        assert ErrorCode.STOCK_CANNOT_REVERSE_REVERSAL == "stock.cannot_reverse_reversal"

    def test_stock_reverse_would_go_negative(self) -> None:
        from app.core.errors import ErrorCode

        assert ErrorCode.STOCK_REVERSE_WOULD_GO_NEGATIVE == "stock.reverse_would_go_negative"

    def test_tracking_mode_change_conflict(self) -> None:
        from app.core.errors import ErrorCode

        assert (
            ErrorCode.ITEM_DEFINITION_TRACKING_MODE_CHANGE_CONFLICT
            == "item_definition.tracking_mode_change_conflict"
        )


# ---------------------------------------------------------------------------
# 2. Repository: list_active_lots_for_definition
# ---------------------------------------------------------------------------


class TestListActiveLotsForDefinition:
    """StockInstanceRepository.list_active_lots_for_definition FIFO ordering."""

    def test_returns_only_positive_quantity_lots(self, db_session: Session) -> None:
        """Lots with quantity = 0 are excluded from the FIFO list."""
        defn = _seed_definition(db_session, mode="exact")
        now = datetime.now(tz=UTC)

        lot_active = _seed_exact_lot(db_session, defn.id, Decimal("5"), received_at=now)
        lot_zero = _seed_exact_lot(
            db_session,
            defn.id,
            Decimal("0"),
            received_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        db_session.commit()

        from app.repositories.stock_instance import StockInstanceRepository

        repo = StockInstanceRepository(db_session)
        lots = repo.list_active_lots_for_definition(defn.id)
        ids = [lot.id for lot in lots]
        assert lot_active.id in ids  # type: ignore[attr-defined]
        assert lot_zero.id not in ids  # type: ignore[attr-defined]

    def test_ordered_by_received_at_then_id(self, db_session: Session) -> None:
        """list_active_lots returns lots ordered by (received_at ASC, id ASC)."""
        defn = _seed_definition(db_session, mode="exact")

        t1 = datetime(2021, 1, 1, tzinfo=UTC)
        t2 = datetime(2022, 6, 15, tzinfo=UTC)
        t3 = datetime(2023, 3, 1, tzinfo=UTC)

        # Insert in reverse chronological order to prove ordering is by received_at, not id.
        lot_newest = _seed_exact_lot(db_session, defn.id, Decimal("3"), received_at=t3)
        lot_middle = _seed_exact_lot(db_session, defn.id, Decimal("2"), received_at=t2)
        lot_oldest = _seed_exact_lot(db_session, defn.id, Decimal("4"), received_at=t1)
        db_session.commit()

        from app.repositories.stock_instance import StockInstanceRepository

        repo = StockInstanceRepository(db_session)
        lots = repo.list_active_lots_for_definition(defn.id)
        ids = [lot.id for lot in lots]
        # Expected order: oldest received_at first.
        assert ids == [lot_oldest.id, lot_middle.id, lot_newest.id]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 3. Intake
# ---------------------------------------------------------------------------


class TestIntake:
    """StockMovementService.intake."""

    def test_intake_adds_to_quantity(self, db_session: Session) -> None:
        """intake appends movement and quantity increases."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("5"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.intake(lot, Decimal("3"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot)

        assert lot.quantity == Decimal("8")  # type: ignore[attr-defined]

    def test_intake_ledger_invariant(self, db_session: Session) -> None:
        """After intake: quantity == SUM(quantity_delta)."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("10"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.repositories.stock_movement import StockMovementRepository
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.intake(lot, Decimal("4"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot)

        repo = StockMovementRepository(db_session)
        assert lot.quantity == repo.sum_delta_for_instance(lot.id)  # type: ignore[attr-defined]

    def test_intake_non_positive_rejected(self, db_session: Session) -> None:
        """intake with quantity <= 0 raises stock.negative_quantity."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("5"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        with pytest.raises(AppError) as exc_info:
            svc.intake(lot, Decimal("0"))  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.STOCK_NEGATIVE_QUANTITY
        assert exc_info.value.status_code == 422

    def test_intake_negative_rejected(self, db_session: Session) -> None:
        """intake with negative quantity is rejected."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("5"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        with pytest.raises(AppError) as exc_info:
            svc.intake(lot, Decimal("-1"))  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.STOCK_NEGATIVE_QUANTITY

    def test_intake_level_mode_rejected(self, db_session: Session) -> None:
        """intake on a level-mode definition raises stock.movement_not_applicable."""
        defn = _seed_definition(db_session, mode="level")
        from app.models.stock_instance import StockInstance

        # Create a stub instance (no ledger operations for level mode).
        inst = StockInstance(definition_id=defn.id, stock_level="high")  # type: ignore[attr-defined]
        db_session.add(inst)
        db_session.flush()
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        with pytest.raises(AppError) as exc_info:
            svc.intake(inst, Decimal("1"))
        assert exc_info.value.code == ErrorCode.STOCK_MOVEMENT_NOT_APPLICABLE
        assert exc_info.value.status_code == 409

    def test_intake_none_mode_rejected(self, db_session: Session) -> None:
        """intake on a none-mode definition raises stock.movement_not_applicable."""
        defn = _seed_definition(db_session, mode="none")
        from app.models.stock_instance import StockInstance

        inst = StockInstance(definition_id=defn.id)  # type: ignore[attr-defined]
        db_session.add(inst)
        db_session.flush()
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        with pytest.raises(AppError) as exc_info:
            svc.intake(inst, Decimal("1"))
        assert exc_info.value.code == ErrorCode.STOCK_MOVEMENT_NOT_APPLICABLE


# ---------------------------------------------------------------------------
# 4. Serial⇒qty=1 via intake
# ---------------------------------------------------------------------------


class TestSerialQty1ViaIntake:
    """An intake that would push a serialized lot above 1 is rejected."""

    def test_intake_pushes_serial_above_1_rejected(self, db_session: Session) -> None:
        """intake on a serialized lot with qty=1 that would result in qty=2 → 422."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("1"), serial="SN-001")
        db_session.commit()
        db_session.refresh(lot)

        assert lot.quantity == Decimal("1")  # type: ignore[attr-defined]

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        with pytest.raises(AppError) as exc_info:
            svc.intake(lot, Decimal("1"))
        assert exc_info.value.code == ErrorCode.STOCK_INSTANCE_SERIAL_REQUIRES_QTY_ONE
        assert exc_info.value.status_code == 422

        # The lot quantity must be unchanged (transaction integrity).
        db_session.rollback()
        db_session.refresh(lot)
        assert lot.quantity == Decimal("1")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 5. Discard
# ---------------------------------------------------------------------------


class TestDiscard:
    """StockMovementService.discard."""

    def test_discard_subtracts_from_quantity(self, db_session: Session) -> None:
        """discard appends movement and quantity decreases."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("10"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.discard(lot, Decimal("3"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot)

        assert lot.quantity == Decimal("7")  # type: ignore[attr-defined]

    def test_discard_below_zero_rejected(self, db_session: Session) -> None:
        """discard that would go below 0 raises stock.negative_quantity."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("5"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        with pytest.raises(AppError) as exc_info:
            svc.discard(lot, Decimal("6"))  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.STOCK_NEGATIVE_QUANTITY
        assert exc_info.value.status_code == 422

    def test_discard_non_positive_rejected(self, db_session: Session) -> None:
        """discard with quantity <= 0 raises stock.negative_quantity."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("5"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        with pytest.raises(AppError) as exc_info:
            svc.discard(lot, Decimal("0"))  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.STOCK_NEGATIVE_QUANTITY

    def test_discard_ledger_invariant(self, db_session: Session) -> None:
        """After discard: quantity == SUM(quantity_delta)."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("10"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.repositories.stock_movement import StockMovementRepository
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.discard(lot, Decimal("4"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot)

        repo = StockMovementRepository(db_session)
        assert lot.quantity == repo.sum_delta_for_instance(lot.id)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 6. Adjust
# ---------------------------------------------------------------------------


class TestAdjust:
    """StockMovementService.adjust."""

    def test_adjust_to_higher_value(self, db_session: Session) -> None:
        """adjust to a higher counted value appends positive delta."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("5"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.adjust(lot, Decimal("8"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot)

        assert lot.quantity == Decimal("8")  # type: ignore[attr-defined]

    def test_adjust_to_lower_value(self, db_session: Session) -> None:
        """adjust to a lower counted value appends negative delta."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("10"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.adjust(lot, Decimal("3"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot)

        assert lot.quantity == Decimal("3")  # type: ignore[attr-defined]

    def test_adjust_to_zero(self, db_session: Session) -> None:
        """adjust to 0 sets quantity to 0 (allowed; counted_quantity >= 0)."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("5"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.adjust(lot, Decimal("0"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot)

        assert lot.quantity == Decimal("0")  # type: ignore[attr-defined]

    def test_adjust_negative_counted_rejected(self, db_session: Session) -> None:
        """adjust with counted_quantity < 0 raises stock.negative_quantity."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("5"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        with pytest.raises(AppError) as exc_info:
            svc.adjust(lot, Decimal("-1"))  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.STOCK_NEGATIVE_QUANTITY
        assert exc_info.value.status_code == 422

    def test_adjust_correct_signed_delta(self, db_session: Session) -> None:
        """adjust appends a movement with delta = counted - current."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("7"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.models.stock_movement import StockMovement
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.adjust(lot, Decimal("4"))  # type: ignore[arg-type]
        db_session.commit()

        # Find the adjust movement (type='adjust').
        movements = db_session.scalars(
            select(StockMovement).where(
                StockMovement.instance_id == lot.id, StockMovement.type == "adjust"
            )  # type: ignore[attr-defined]
        ).all()
        assert len(movements) == 1
        assert movements[0].quantity_delta == Decimal("-3")  # 4 - 7 = -3

    def test_adjust_ledger_invariant(self, db_session: Session) -> None:
        """After adjust: quantity == SUM(quantity_delta)."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("10"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.repositories.stock_movement import StockMovementRepository
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.adjust(lot, Decimal("6"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot)

        repo = StockMovementRepository(db_session)
        assert lot.quantity == repo.sum_delta_for_instance(lot.id)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 7. Move
# ---------------------------------------------------------------------------


class TestMove:
    """StockMovementService.move."""

    def test_move_changes_location_id(self, db_session: Session) -> None:
        """move updates inst.location_id to to_location_id."""
        defn = _seed_definition(db_session, mode="exact")
        loc_a = _seed_location(db_session, "Shelf A")
        loc_b = _seed_location(db_session, "Shelf B")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("5"), location_id=loc_a.id)  # type: ignore[attr-defined]
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.move(lot, loc_b.id)  # type: ignore[arg-type, attr-defined]
        db_session.commit()
        db_session.refresh(lot)

        assert lot.location_id == loc_b.id  # type: ignore[attr-defined]

    def test_move_records_delta_zero(self, db_session: Session) -> None:
        """move movement has quantity_delta = 0."""
        defn = _seed_definition(db_session, mode="exact")
        loc_a = _seed_location(db_session, "A")
        loc_b = _seed_location(db_session, "B")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("5"), location_id=loc_a.id)  # type: ignore[attr-defined]
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.models.stock_movement import StockMovement
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.move(lot, loc_b.id)  # type: ignore[attr-defined]
        db_session.commit()

        move_movements = db_session.scalars(
            select(StockMovement).where(
                StockMovement.instance_id == lot.id, StockMovement.type == "move"
            )  # type: ignore[attr-defined]
        ).all()
        assert len(move_movements) == 1
        assert move_movements[0].quantity_delta == Decimal("0")
        assert move_movements[0].from_location_id == loc_a.id  # type: ignore[attr-defined]
        assert move_movements[0].to_location_id == loc_b.id  # type: ignore[attr-defined]

    def test_move_quantity_unchanged(self, db_session: Session) -> None:
        """move does not change the lot's quantity."""
        defn = _seed_definition(db_session, mode="exact")
        loc_a = _seed_location(db_session, "A")
        loc_b = _seed_location(db_session, "B")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("7"), location_id=loc_a.id)  # type: ignore[attr-defined]
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.move(lot, loc_b.id)  # type: ignore[attr-defined]
        db_session.commit()
        db_session.refresh(lot)

        assert lot.quantity == Decimal("7")  # type: ignore[attr-defined]

    def test_move_nonexistent_to_location_raises_404(self, db_session: Session) -> None:
        """move to a non-existent location_id raises 404."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("5"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        with pytest.raises(AppError) as exc_info:
            svc.move(lot, 99999)  # type: ignore[arg-type]
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == ErrorCode.LOCATION_NOT_FOUND


# ---------------------------------------------------------------------------
# 8. FIFO consume
# ---------------------------------------------------------------------------


class TestConsumeFixo:
    """StockMovementService.consume_fifo — the headline operation."""

    def test_consume_single_lot(self, db_session: Session) -> None:
        """consume_fifo on a single lot subtracts quantity correctly."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("10"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        touched = svc.consume_fifo(defn, Decimal("3"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot)

        assert lot.quantity == Decimal("7")  # type: ignore[attr-defined]
        assert len(touched) == 1
        assert touched[0].id == lot.id  # type: ignore[attr-defined]

    def test_consume_fifo_multi_lot_ordering(self, db_session: Session) -> None:
        """consume_fifo walks lots oldest received_at first, NOT insertion order."""
        defn = _seed_definition(db_session, mode="exact")

        t_oldest = datetime(2021, 1, 1, tzinfo=UTC)
        t_newest = datetime(2023, 1, 1, tzinfo=UTC)

        # Insert newest first (insertion order ≠ FIFO order).
        lot_new = _seed_exact_lot(db_session, defn.id, Decimal("5"), received_at=t_newest)
        lot_old = _seed_exact_lot(db_session, defn.id, Decimal("5"), received_at=t_oldest)
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        # Consume 5 — should fully drain the oldest lot.
        svc.consume_fifo(defn, Decimal("5"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot_old)
        db_session.refresh(lot_new)

        assert lot_old.quantity == Decimal("0"), "Oldest lot should be drained first"  # type: ignore[attr-defined]
        assert lot_new.quantity == Decimal("5"), "Newest lot should be untouched"  # type: ignore[attr-defined]

    def test_consume_fifo_spans_multiple_lots(self, db_session: Session) -> None:
        """consume_fifo spanning two lots touches both in FIFO order."""
        defn = _seed_definition(db_session, mode="exact")

        t1 = datetime(2021, 1, 1, tzinfo=UTC)
        t2 = datetime(2022, 1, 1, tzinfo=UTC)

        lot1 = _seed_exact_lot(db_session, defn.id, Decimal("3"), received_at=t1)
        lot2 = _seed_exact_lot(db_session, defn.id, Decimal("7"), received_at=t2)
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        # Consume 5: drains lot1 (3), takes 2 from lot2.
        touched = svc.consume_fifo(defn, Decimal("5"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot1)
        db_session.refresh(lot2)

        assert lot1.quantity == Decimal("0")  # type: ignore[attr-defined]
        assert lot2.quantity == Decimal("5")  # type: ignore[attr-defined]
        assert len(touched) == 2

    def test_consume_fifo_decimal_precision(self, db_session: Session) -> None:
        """FIFO consume uses exact Decimal arithmetic (no float rounding)."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("10"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.consume_fifo(defn, Decimal("3.333333"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot)

        assert lot.quantity == Decimal("6.666667")  # type: ignore[attr-defined]

    def test_consume_fifo_insufficient_stock_rejected(self, db_session: Session) -> None:
        """consume_fifo raises stock.insufficient when total stock < requested."""
        defn = _seed_definition(db_session, mode="exact")
        _seed_exact_lot(db_session, defn.id, Decimal("3"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        with pytest.raises(AppError) as exc_info:
            svc.consume_fifo(defn, Decimal("10"))  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.STOCK_INSUFFICIENT
        assert exc_info.value.status_code == 422
        params = exc_info.value.params or {}
        assert Decimal(str(params["requested"])) == Decimal("10")
        assert Decimal(str(params["available"])) == Decimal("3")

    def test_consume_fifo_insufficient_writes_nothing(self, db_session: Session) -> None:
        """Insufficient consume writes NOTHING — transaction integrity holds."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("3"))
        db_session.commit()

        from app.models.stock_movement import StockMovement

        movements_before = db_session.scalars(
            select(StockMovement).where(StockMovement.instance_id == lot.id)  # type: ignore[attr-defined]
        ).all()
        count_before = len(movements_before)

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        with pytest.raises(AppError):
            svc.consume_fifo(defn, Decimal("10"))  # type: ignore[arg-type]

        # No new movements should have been flushed.
        movements_after = db_session.scalars(
            select(StockMovement).where(StockMovement.instance_id == lot.id)  # type: ignore[attr-defined]
        ).all()
        assert len(movements_after) == count_before

        # Quantity unchanged.
        db_session.refresh(lot)
        assert lot.quantity == Decimal("3")  # type: ignore[attr-defined]

    def test_consume_fifo_level_mode_rejected(self, db_session: Session) -> None:
        """consume_fifo on a level-mode definition raises stock.movement_not_applicable."""
        defn = _seed_definition(db_session, mode="level")
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        with pytest.raises(AppError) as exc_info:
            svc.consume_fifo(defn, Decimal("1"))  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.STOCK_MOVEMENT_NOT_APPLICABLE

    def test_consume_fifo_none_mode_rejected(self, db_session: Session) -> None:
        """consume_fifo on a none-mode definition raises stock.movement_not_applicable."""
        defn = _seed_definition(db_session, mode="none")
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        with pytest.raises(AppError) as exc_info:
            svc.consume_fifo(defn, Decimal("1"))  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.STOCK_MOVEMENT_NOT_APPLICABLE

    def test_consume_fifo_zero_quantity_rejected(self, db_session: Session) -> None:
        """consume_fifo with quantity = 0 raises stock.negative_quantity."""
        defn = _seed_definition(db_session, mode="exact")
        _seed_exact_lot(db_session, defn.id, Decimal("5"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        with pytest.raises(AppError) as exc_info:
            svc.consume_fifo(defn, Decimal("0"))  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.STOCK_NEGATIVE_QUANTITY

    def test_consume_fifo_skips_zero_quantity_lots(self, db_session: Session) -> None:
        """FIFO skips lots with quantity = 0 and consumes from non-zero lots."""
        defn = _seed_definition(db_session, mode="exact")

        t_old = datetime(2020, 1, 1, tzinfo=UTC)
        t_new = datetime(2022, 1, 1, tzinfo=UTC)

        # Oldest lot has 0 quantity (empty lot kept after prior consumption).
        lot_zero = _seed_exact_lot(db_session, defn.id, Decimal("0"), received_at=t_old)
        lot_active = _seed_exact_lot(db_session, defn.id, Decimal("5"), received_at=t_new)
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.consume_fifo(defn, Decimal("3"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot_zero)
        db_session.refresh(lot_active)

        assert lot_zero.quantity == Decimal("0"), "Empty lot should not be touched"  # type: ignore[attr-defined]
        assert lot_active.quantity == Decimal("2")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 9. Reversal / undo
# ---------------------------------------------------------------------------


class TestReverse:
    """StockMovementService.reverse."""

    def test_reverse_consume_restores_quantity(self, db_session: Session) -> None:
        """Reversing a consume movement restores the lot's quantity."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("10"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.models.stock_movement import StockMovement
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.consume_fifo(defn, Decimal("3"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot)
        assert lot.quantity == Decimal("7")  # type: ignore[attr-defined]

        # Find the consume movement.
        consume_m = db_session.scalars(
            select(StockMovement).where(
                StockMovement.instance_id == lot.id, StockMovement.type == "consume"
            )  # type: ignore[attr-defined]
        ).first()
        assert consume_m is not None

        # Reverse it.
        svc.reverse(consume_m.id)
        db_session.commit()
        db_session.refresh(lot)

        assert lot.quantity == Decimal("10")  # type: ignore[attr-defined]

    def test_reverse_appends_correction_movement(self, db_session: Session) -> None:
        """reverse appends a correction movement with delta = -original.delta."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("10"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.models.stock_movement import StockMovement
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.consume_fifo(defn, Decimal("4"))  # type: ignore[arg-type]
        db_session.commit()

        consume_m = db_session.scalars(
            select(StockMovement).where(
                StockMovement.instance_id == lot.id, StockMovement.type == "consume"
            )  # type: ignore[attr-defined]
        ).first()
        assert consume_m is not None
        original_delta = consume_m.quantity_delta

        svc.reverse(consume_m.id)
        db_session.commit()

        correction_m = db_session.scalars(
            select(StockMovement).where(StockMovement.reverses_movement_id == consume_m.id)
        ).first()
        assert correction_m is not None
        assert correction_m.type == "correction"
        assert correction_m.quantity_delta == -original_delta
        assert correction_m.reverses_movement_id == consume_m.id

    def test_double_reverse_raises_already_reversed(self, db_session: Session) -> None:
        """Reversing a movement twice raises stock.movement_already_reversed."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("10"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.models.stock_movement import StockMovement
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.consume_fifo(defn, Decimal("3"))  # type: ignore[arg-type]
        db_session.commit()

        consume_m = db_session.scalars(
            select(StockMovement).where(
                StockMovement.instance_id == lot.id, StockMovement.type == "consume"
            )  # type: ignore[attr-defined]
        ).first()
        assert consume_m is not None

        # First reverse — should succeed.
        svc.reverse(consume_m.id)
        db_session.commit()

        # Second reverse of the same movement — should fail.
        with pytest.raises(AppError) as exc_info:
            svc.reverse(consume_m.id)
        assert exc_info.value.code == ErrorCode.STOCK_MOVEMENT_ALREADY_REVERSED
        assert exc_info.value.status_code == 409

    def test_reverse_of_reversal_rejected(self, db_session: Session) -> None:
        """Reversing a correction (which is itself a reversal) raises cannot_reverse_reversal."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("10"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.models.stock_movement import StockMovement
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.consume_fifo(defn, Decimal("3"))  # type: ignore[arg-type]
        db_session.commit()

        consume_m = db_session.scalars(
            select(StockMovement).where(
                StockMovement.instance_id == lot.id, StockMovement.type == "consume"
            )  # type: ignore[attr-defined]
        ).first()
        assert consume_m is not None

        svc.reverse(consume_m.id)
        db_session.commit()

        correction_m = db_session.scalars(
            select(StockMovement).where(StockMovement.reverses_movement_id == consume_m.id)
        ).first()
        assert correction_m is not None

        with pytest.raises(AppError) as exc_info:
            svc.reverse(correction_m.id)
        assert exc_info.value.code == ErrorCode.STOCK_CANNOT_REVERSE_REVERSAL
        assert exc_info.value.status_code == 409

    def test_reverse_would_go_negative_rejected(self, db_session: Session) -> None:
        """Reversing an intake when stock was consumed would go negative → rejected."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("5"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.models.stock_movement import StockMovement
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        # Consume 3 → qty = 2.
        svc.consume_fifo(defn, Decimal("3"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot)
        assert lot.quantity == Decimal("2")  # type: ignore[attr-defined]

        # Find the original intake movement (delta = +5).
        intake_m = db_session.scalars(
            select(StockMovement).where(
                StockMovement.instance_id == lot.id, StockMovement.type == "intake"
            )  # type: ignore[attr-defined]
        ).first()
        assert intake_m is not None

        # Reversing the +5 intake would give 2 - 5 = -3, which is < 0 → rejected.
        with pytest.raises(AppError) as exc_info:
            svc.reverse(intake_m.id)
        assert exc_info.value.code == ErrorCode.STOCK_REVERSE_WOULD_GO_NEGATIVE
        assert exc_info.value.status_code == 409

    def test_reverse_move_restores_location(self, db_session: Session) -> None:
        """Reversing a move movement restores the lot's location_id."""
        defn = _seed_definition(db_session, mode="exact")
        loc_a = _seed_location(db_session, "A")
        loc_b = _seed_location(db_session, "B")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("5"), location_id=loc_a.id)  # type: ignore[attr-defined]
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.models.stock_movement import StockMovement
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.move(lot, loc_b.id)  # type: ignore[attr-defined]
        db_session.commit()
        db_session.refresh(lot)
        assert lot.location_id == loc_b.id  # type: ignore[attr-defined]

        move_m = db_session.scalars(
            select(StockMovement).where(
                StockMovement.instance_id == lot.id, StockMovement.type == "move"
            )  # type: ignore[attr-defined]
        ).first()
        assert move_m is not None

        svc.reverse(move_m.id)
        db_session.commit()
        db_session.refresh(lot)

        # Location should be restored to loc_a.
        assert lot.location_id == loc_a.id  # type: ignore[attr-defined]

    def test_reverse_nonexistent_movement_raises_404(self, db_session: Session) -> None:
        """Reversing a non-existent movement id raises stock.movement_not_found."""
        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        with pytest.raises(AppError) as exc_info:
            svc.reverse(99999)
        assert exc_info.value.code == ErrorCode.STOCK_MOVEMENT_NOT_FOUND
        assert exc_info.value.status_code == 404

    def test_reverse_ledger_invariant(self, db_session: Session) -> None:
        """After reverse: quantity == SUM(quantity_delta)."""
        defn = _seed_definition(db_session, mode="exact")
        lot = _seed_exact_lot(db_session, defn.id, Decimal("10"))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.models.stock_movement import StockMovement
        from app.repositories.stock_movement import StockMovementRepository
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]
        svc.consume_fifo(defn, Decimal("3"))  # type: ignore[arg-type]
        db_session.commit()

        consume_m = db_session.scalars(
            select(StockMovement).where(
                StockMovement.instance_id == lot.id, StockMovement.type == "consume"
            )  # type: ignore[attr-defined]
        ).first()
        assert consume_m is not None
        svc.reverse(consume_m.id)
        db_session.commit()
        db_session.refresh(lot)

        repo = StockMovementRepository(db_session)
        assert lot.quantity == repo.sum_delta_for_instance(lot.id)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 10. Mode-change guard (ItemDefinitionService)
# ---------------------------------------------------------------------------


class TestModeChangeGuard:
    """mode-change guard in ItemDefinitionService.update."""

    def test_mode_change_on_populated_definition_rejected(self, db_session: Session) -> None:
        """PATCH definition mode when it has lots → tracking_mode_change_conflict (409)."""
        defn = _seed_definition(db_session, mode="exact")
        _seed_exact_lot(db_session, defn.id, Decimal("5"))
        db_session.commit()

        from app.core.errors import AppError, ErrorCode
        from app.schemas.item_definition import DefinitionUpdate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.update(defn.id, DefinitionUpdate(stock_tracking_mode="none"))  # type: ignore[attr-defined]
        assert exc_info.value.code == ErrorCode.ITEM_DEFINITION_TRACKING_MODE_CHANGE_CONFLICT
        assert exc_info.value.status_code == 409
        params = exc_info.value.params or {}
        assert params.get("id") == defn.id  # type: ignore[attr-defined]
        assert params.get("from") == "exact"
        assert params.get("to") == "none"

    def test_mode_change_on_empty_definition_allowed(self, db_session: Session) -> None:
        """PATCH definition mode when it has NO lots → succeeds."""
        defn = _seed_definition(db_session, mode="exact")
        db_session.commit()

        from app.schemas.item_definition import DefinitionUpdate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        updated = svc.update(defn.id, DefinitionUpdate(stock_tracking_mode="none"))  # type: ignore[attr-defined]
        db_session.commit()
        db_session.refresh(updated)
        assert updated.stock_tracking_mode == "none"

    def test_same_mode_patch_is_not_a_mode_change(self, db_session: Session) -> None:
        """PATCH with the same mode value on a populated definition → allowed."""
        defn = _seed_definition(db_session, mode="exact")
        _seed_exact_lot(db_session, defn.id, Decimal("5"))
        db_session.commit()

        from app.schemas.item_definition import DefinitionUpdate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        # Sending the same mode is a no-op and must not raise.
        updated = svc.update(defn.id, DefinitionUpdate(stock_tracking_mode="exact"))  # type: ignore[attr-defined]
        db_session.commit()
        db_session.refresh(updated)
        assert updated.stock_tracking_mode == "exact"


# ---------------------------------------------------------------------------
# 11. HTTP API (end-to-end)
# ---------------------------------------------------------------------------


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite DB for HTTP-level tests."""
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m2-step4")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def test_client(temp_db: Path) -> Generator[object]:  # noqa: ARG001
    """TestClient with full schema + authenticated admin session."""
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


def _api_create_definition(client: object, name: str, **kwargs: object) -> dict:  # type: ignore[type-arg]
    from fastapi.testclient import TestClient

    assert isinstance(client, TestClient)
    resp = client.post("/api/definitions", json={"name": name, **kwargs})
    assert resp.status_code == 201, f"create_definition failed: {resp.text}"
    return resp.json()  # type: ignore[return-value]


def _api_create_instance(
    client: object,
    definition_id: int,
    *,
    expect_status: int = 201,
    **kwargs: object,
) -> dict:  # type: ignore[type-arg]
    from fastapi.testclient import TestClient

    assert isinstance(client, TestClient)
    resp = client.post("/api/instances", json={"definition_id": definition_id, **kwargs})
    assert resp.status_code == expect_status, (
        f"create_instance failed: {resp.status_code} {resp.text}"
    )
    return resp.json()  # type: ignore[return-value]


def _api_create_location(client: object, name: str) -> dict:  # type: ignore[type-arg]
    from fastapi.testclient import TestClient

    assert isinstance(client, TestClient)
    resp = client.post("/api/locations", json={"name": name})
    assert resp.status_code == 201, f"create_location failed: {resp.text}"
    return resp.json()  # type: ignore[return-value]


class TestHTTPMovementEndpoints:
    """HTTP-level tests for the movement operation endpoints."""

    def test_intake_adds_quantity(self, test_client: object) -> None:
        """POST /instances/{id}/intake returns updated InstanceResponse with new quantity."""
        defn = _api_create_definition(test_client, "Nails", stock_tracking_mode="exact")
        inst = _api_create_instance(test_client, defn["id"], quantity="5")

        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        resp = test_client.post(f"/api/instances/{inst['id']}/intake", json={"quantity": "3"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert Decimal(data["quantity"]) == Decimal("8")

    def test_discard_subtracts_quantity(self, test_client: object) -> None:
        """POST /instances/{id}/discard returns updated InstanceResponse."""
        defn = _api_create_definition(test_client, "Bolts", stock_tracking_mode="exact")
        inst = _api_create_instance(test_client, defn["id"], quantity="10")

        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        resp = test_client.post(f"/api/instances/{inst['id']}/discard", json={"quantity": "4"})
        assert resp.status_code == 200, resp.text
        assert Decimal(resp.json()["quantity"]) == Decimal("6")

    def test_discard_below_zero_returns_422(self, test_client: object) -> None:
        """POST /instances/{id}/discard that would go negative returns 422."""
        defn = _api_create_definition(test_client, "Pins", stock_tracking_mode="exact")
        inst = _api_create_instance(test_client, defn["id"], quantity="2")

        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        resp = test_client.post(f"/api/instances/{inst['id']}/discard", json={"quantity": "5"})
        assert resp.status_code == 422
        assert resp.json()["code"] == "stock.negative_quantity"

    def test_adjust_sets_absolute_quantity(self, test_client: object) -> None:
        """POST /instances/{id}/adjust sets quantity to the counted value."""
        defn = _api_create_definition(test_client, "Screws", stock_tracking_mode="exact")
        inst = _api_create_instance(test_client, defn["id"], quantity="10")

        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        resp = test_client.post(f"/api/instances/{inst['id']}/adjust", json={"quantity": "6"})
        assert resp.status_code == 200, resp.text
        assert Decimal(resp.json()["quantity"]) == Decimal("6")

    def test_move_changes_location(self, test_client: object) -> None:
        """POST /instances/{id}/move changes the location_id."""
        loc_a = _api_create_location(test_client, "Shelf A")
        loc_b = _api_create_location(test_client, "Shelf B")
        defn = _api_create_definition(test_client, "Widgets", stock_tracking_mode="exact")
        inst = _api_create_instance(test_client, defn["id"], quantity="5", location_id=loc_a["id"])

        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        resp = test_client.post(
            f"/api/instances/{inst['id']}/move",
            json={"to_location_id": loc_b["id"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["location_id"] == loc_b["id"]

    def test_get_movements_returns_history(self, test_client: object) -> None:
        """GET /instances/{id}/movements returns movement history newest-first."""
        defn = _api_create_definition(test_client, "Items", stock_tracking_mode="exact")
        inst = _api_create_instance(test_client, defn["id"], quantity="10")

        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        test_client.post(f"/api/instances/{inst['id']}/discard", json={"quantity": "3"})

        resp = test_client.get(f"/api/instances/{inst['id']}/movements")
        assert resp.status_code == 200, resp.text
        movements = resp.json()
        # Should have at least 2 movements: initial intake + discard.
        assert len(movements) >= 2
        # Newest first: discard should be before intake.
        types = [m["type"] for m in movements]
        assert types[0] == "discard"

    def test_consume_fifo_endpoint(self, test_client: object) -> None:
        """POST /definitions/{id}/consume returns list of touched InstanceResponses."""
        defn = _api_create_definition(test_client, "AA Batteries", stock_tracking_mode="exact")
        _api_create_instance(test_client, defn["id"], quantity="10")

        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        resp = test_client.post(f"/api/definitions/{defn['id']}/consume", json={"quantity": "3"})
        assert resp.status_code == 200, resp.text
        touched = resp.json()
        assert isinstance(touched, list)
        assert len(touched) == 1
        assert Decimal(touched[0]["quantity"]) == Decimal("7")

    def test_consume_fifo_insufficient_returns_422(self, test_client: object) -> None:
        """POST /definitions/{id}/consume with insufficient stock returns 422."""
        defn = _api_create_definition(test_client, "Rare Widgets", stock_tracking_mode="exact")
        _api_create_instance(test_client, defn["id"], quantity="2")

        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        resp = test_client.post(f"/api/definitions/{defn['id']}/consume", json={"quantity": "10"})
        assert resp.status_code == 422
        assert resp.json()["code"] == "stock.insufficient"

    def test_reverse_endpoint(self, test_client: object) -> None:
        """POST /movements/{id}/reverse returns updated InstanceResponse with restored quantity."""
        defn = _api_create_definition(test_client, "Caps", stock_tracking_mode="exact")
        inst = _api_create_instance(test_client, defn["id"], quantity="10")

        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        # Consume 3.
        test_client.post(f"/api/definitions/{defn['id']}/consume", json={"quantity": "3"})

        # Get the consume movement.
        movements = test_client.get(f"/api/instances/{inst['id']}/movements").json()
        consume_m = next(m for m in movements if m["type"] == "consume")

        resp = test_client.post(f"/api/movements/{consume_m['id']}/reverse", json={})
        assert resp.status_code == 200, resp.text
        assert Decimal(resp.json()["quantity"]) == Decimal("10")

    def test_reverse_not_found_returns_404(self, test_client: object) -> None:
        """POST /movements/99999/reverse returns 404."""
        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        resp = test_client.post("/api/movements/99999/reverse", json={})
        assert resp.status_code == 404
        assert resp.json()["code"] == "stock.movement_not_found"

    def test_mode_change_conflict_endpoint(self, test_client: object) -> None:
        """PATCH /definitions/{id} changing mode on a populated definition returns 409."""
        defn = _api_create_definition(test_client, "Items", stock_tracking_mode="exact")
        _api_create_instance(test_client, defn["id"], quantity="5")

        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"stock_tracking_mode": "none"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "item_definition.tracking_mode_change_conflict"

    def test_mode_change_on_empty_definition_succeeds(self, test_client: object) -> None:
        """PATCH /definitions/{id} changing mode on an empty definition succeeds."""
        defn = _api_create_definition(test_client, "EmptyItems", stock_tracking_mode="exact")

        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"stock_tracking_mode": "none"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["stock_tracking_mode"] == "none"

    def test_movement_not_applicable_on_level_mode(self, test_client: object) -> None:
        """POST /instances/{id}/intake on a level-mode instance returns 409."""
        defn = _api_create_definition(test_client, "Screws", stock_tracking_mode="level")
        inst = _api_create_instance(test_client, defn["id"], stock_level="high")

        from fastapi.testclient import TestClient

        assert isinstance(test_client, TestClient)
        resp = test_client.post(f"/api/instances/{inst['id']}/intake", json={"quantity": "1"})
        assert resp.status_code == 409
        assert resp.json()["code"] == "stock.movement_not_applicable"
