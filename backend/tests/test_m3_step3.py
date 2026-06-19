"""M3 Step 3 tests: FEFO consumption ordering by best-before date.

Required coverage (per M3.md §5 "Backend" + §9 Step 3 "Tests" + §10 blind-review
checkpoints):

FEFO ordering (the easy-to-get-wrong consumption order — §4.3, §5):
- nearest-expiry lot consumed first, even when it was received LATER than a
  farther-expiry lot (proves best_before_date beats received_at);
- NULL-best_before_date lot consumed LAST (NULLS-LAST via the portable
  ``best_before_date IS NULL`` leading key, not a dialect-specific clause);
- same best_before_date falls back to received_at then id tie-break;
- multi-lot span: consumption walks across multiple lots in FEFO order;
- partial-lot Decimal precision preserved (M2 invariant, unbroken);
- insufficient total stock rejected with NOTHING written (M2 invariant, unbroken).

Repository-level (list_active_lots_for_definition):
- ordering key is FEFO (best_before_date ASC NULLS LAST, received_at, id);
- WHERE clause unchanged: only quantity > 0 lots returned.
"""

from __future__ import annotations

import importlib
from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Helpers
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
    """Fresh in-memory SQLite session with all models registered and kinds seeded."""
    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
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


def _seed_exact_lot(
    session: Session,
    definition_id: int,
    quantity: Decimal,
    *,
    received_at: datetime | None = None,
    best_before_date: date | None = None,
) -> object:
    """Seed a stock instance (exact mode) with optional best_before_date and received_at."""
    from app.models.stock_instance import StockInstance
    from app.repositories.stock_movement import StockMovementRepository
    from app.services.stock_instance import StockInstanceService

    inst = StockInstance(
        definition_id=definition_id,
        quantity=None,
        best_before_date=best_before_date,
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
    )
    svc = StockInstanceService(session)
    svc.recompute_quantity(inst)
    session.flush()
    return inst


def _make_ctx(session: Session) -> object:
    """Build a minimal RequestContext with a seeded user and household."""
    from app.core.context import RequestContext
    from app.models.household import Household
    from app.models.user import User

    user = User(email="test@example.com", password_hash="hash")
    session.add(user)
    hh = Household(name="Test HH")
    session.add(hh)
    session.flush()
    return RequestContext(household=hh, user=user)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Repository: list_active_lots_for_definition — FEFO ordering key
# ---------------------------------------------------------------------------


class TestListActiveLotsFefoOrdering:
    """StockInstanceRepository.list_active_lots_for_definition uses FEFO order."""

    def test_nearest_expiry_first_even_when_received_later(self, db_session: Session) -> None:
        """Lot with nearer best_before_date comes first even if received after the far lot."""
        defn = _seed_definition(db_session, mode="exact")

        # t_old received earlier; t_new received later.
        t_old = datetime(2024, 1, 1, tzinfo=UTC)
        t_new = datetime(2024, 6, 1, tzinfo=UTC)

        near_expiry = date(2025, 1, 10)  # expires sooner
        far_expiry = date(2025, 6, 10)  # expires later

        # Seed far-expiry lot received EARLIER; near-expiry received LATER.
        # FEFO must put near_expiry first regardless of received_at.
        lot_far = _seed_exact_lot(
            db_session, defn.id, Decimal("5"), received_at=t_old, best_before_date=far_expiry
        )
        lot_near = _seed_exact_lot(
            db_session, defn.id, Decimal("5"), received_at=t_new, best_before_date=near_expiry
        )
        db_session.commit()

        from app.repositories.stock_instance import StockInstanceRepository

        repo = StockInstanceRepository(db_session)
        lots = repo.list_active_lots_for_definition(defn.id)
        ids = [lot.id for lot in lots]

        # near_expiry must lead, even though it was received later.
        assert ids[0] == lot_near.id, "Nearest-expiry lot must be first"  # type: ignore[attr-defined]
        assert ids[1] == lot_far.id  # type: ignore[attr-defined]

    def test_null_best_before_date_comes_last(self, db_session: Session) -> None:
        """A lot with NULL best_before_date is sorted last (NULLS-LAST, portable)."""
        defn = _seed_definition(db_session, mode="exact")

        t1 = datetime(2024, 1, 1, tzinfo=UTC)
        t2 = datetime(2024, 3, 1, tzinfo=UTC)

        dated_lot = _seed_exact_lot(
            db_session, defn.id, Decimal("5"), received_at=t1, best_before_date=date(2025, 2, 1)
        )
        null_lot = _seed_exact_lot(
            db_session, defn.id, Decimal("5"), received_at=t2, best_before_date=None
        )
        db_session.commit()

        from app.repositories.stock_instance import StockInstanceRepository

        repo = StockInstanceRepository(db_session)
        lots = repo.list_active_lots_for_definition(defn.id)
        ids = [lot.id for lot in lots]

        assert ids[0] == dated_lot.id, "Dated lot must precede NULL lot"  # type: ignore[attr-defined]
        assert ids[-1] == null_lot.id, "NULL best_before_date lot must be last"  # type: ignore[attr-defined]

    def test_same_best_before_date_falls_back_to_received_at(self, db_session: Session) -> None:
        """Same best_before_date: tie-break by received_at ASC."""
        defn = _seed_definition(db_session, mode="exact")

        same_date = date(2025, 3, 15)
        t_earlier = datetime(2024, 1, 1, tzinfo=UTC)
        t_later = datetime(2024, 9, 1, tzinfo=UTC)

        # Insert newer-received first to confirm ordering is NOT insertion order.
        lot_newer = _seed_exact_lot(
            db_session, defn.id, Decimal("4"), received_at=t_later, best_before_date=same_date
        )
        lot_earlier = _seed_exact_lot(
            db_session, defn.id, Decimal("4"), received_at=t_earlier, best_before_date=same_date
        )
        db_session.commit()

        from app.repositories.stock_instance import StockInstanceRepository

        repo = StockInstanceRepository(db_session)
        lots = repo.list_active_lots_for_definition(defn.id)
        ids = [lot.id for lot in lots]

        assert ids[0] == lot_earlier.id, "Earlier received_at must precede later"  # type: ignore[attr-defined]
        assert ids[1] == lot_newer.id  # type: ignore[attr-defined]

    def test_same_best_before_date_same_received_at_falls_back_to_id(
        self, db_session: Session
    ) -> None:
        """Same best_before_date and same received_at: tie-break by id ASC."""
        defn = _seed_definition(db_session, mode="exact")

        same_date = date(2025, 4, 1)
        same_ts = datetime(2024, 5, 1, tzinfo=UTC)

        lot_first = _seed_exact_lot(
            db_session, defn.id, Decimal("3"), received_at=same_ts, best_before_date=same_date
        )
        lot_second = _seed_exact_lot(
            db_session, defn.id, Decimal("3"), received_at=same_ts, best_before_date=same_date
        )
        db_session.commit()

        from app.repositories.stock_instance import StockInstanceRepository

        repo = StockInstanceRepository(db_session)
        lots = repo.list_active_lots_for_definition(defn.id)
        ids = [lot.id for lot in lots]

        # Lower id was inserted first; it must lead.
        assert ids[0] == lot_first.id  # type: ignore[attr-defined]
        assert ids[1] == lot_second.id  # type: ignore[attr-defined]

    def test_where_clause_unchanged_excludes_zero_quantity_lots(self, db_session: Session) -> None:
        """Lots with quantity = 0 are still excluded (WHERE clause from M2 unchanged)."""
        defn = _seed_definition(db_session, mode="exact")

        lot_active = _seed_exact_lot(
            db_session, defn.id, Decimal("5"), best_before_date=date(2025, 1, 1)
        )
        lot_zero = _seed_exact_lot(
            db_session, defn.id, Decimal("0"), best_before_date=date(2024, 6, 1)
        )
        db_session.commit()

        from app.repositories.stock_instance import StockInstanceRepository

        repo = StockInstanceRepository(db_session)
        lots = repo.list_active_lots_for_definition(defn.id)
        ids = [lot.id for lot in lots]

        assert lot_active.id in ids  # type: ignore[attr-defined]
        assert lot_zero.id not in ids  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 2. Service: consume_fifo — FEFO consumption order
# ---------------------------------------------------------------------------


class TestConsumeFefo:
    """StockMovementService.consume_fifo consumes nearest-expiry-first (FEFO)."""

    def test_fefo_nearest_expiry_drained_first_even_if_received_later(
        self, db_session: Session
    ) -> None:
        """Nearest-expiry lot is fully drained before far-expiry lot, regardless of received_at.

        This is the headline FEFO invariant: best_before_date beats received_at.
        """
        defn = _seed_definition(db_session, mode="exact")

        t_old = datetime(2024, 1, 1, tzinfo=UTC)
        t_new = datetime(2024, 6, 1, tzinfo=UTC)

        # Far lot received earlier; near lot received later.
        lot_far = _seed_exact_lot(
            db_session, defn.id, Decimal("5"), received_at=t_old, best_before_date=date(2025, 6, 1)
        )
        lot_near = _seed_exact_lot(
            db_session, defn.id, Decimal("5"), received_at=t_new, best_before_date=date(2025, 1, 1)
        )
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]

        # Consume 5 — must drain the near-expiry lot first, leaving far-expiry untouched.
        svc.consume_fifo(defn, Decimal("5"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot_near)
        db_session.refresh(lot_far)

        assert lot_near.quantity == Decimal("0"), "Nearest-expiry lot must be drained first"  # type: ignore[attr-defined]
        assert lot_far.quantity == Decimal("5"), "Far-expiry lot must be untouched"  # type: ignore[attr-defined]

    def test_null_best_before_lot_consumed_last(self, db_session: Session) -> None:
        """A lot with NULL best_before_date is consumed only after all dated lots."""
        defn = _seed_definition(db_session, mode="exact")

        t1 = datetime(2024, 1, 1, tzinfo=UTC)
        t2 = datetime(2024, 2, 1, tzinfo=UTC)

        lot_null = _seed_exact_lot(
            db_session, defn.id, Decimal("5"), received_at=t1, best_before_date=None
        )
        lot_dated = _seed_exact_lot(
            db_session, defn.id, Decimal("5"), received_at=t2, best_before_date=date(2025, 3, 1)
        )
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]

        # Consume 5 — must drain the dated lot, not the NULL-best-before lot.
        svc.consume_fifo(defn, Decimal("5"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot_null)
        db_session.refresh(lot_dated)

        assert lot_dated.quantity == Decimal("0"), "Dated lot must be consumed first"  # type: ignore[attr-defined]
        assert lot_null.quantity == Decimal("5"), "NULL lot must be untouched"  # type: ignore[attr-defined]

    def test_null_lot_consumed_when_it_is_the_only_lot(self, db_session: Session) -> None:
        """A NULL-best-before lot IS consumed when there are no dated lots ahead of it."""
        defn = _seed_definition(db_session, mode="exact")

        lot_null = _seed_exact_lot(db_session, defn.id, Decimal("10"), best_before_date=None)
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]

        svc.consume_fifo(defn, Decimal("4"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot_null)

        assert lot_null.quantity == Decimal("6")  # type: ignore[attr-defined]

    def test_same_best_before_date_tie_breaks_on_received_at(self, db_session: Session) -> None:
        """Same best_before_date: oldest received_at is consumed first."""
        defn = _seed_definition(db_session, mode="exact")

        same_date = date(2025, 5, 1)
        t_earlier = datetime(2024, 1, 1, tzinfo=UTC)
        t_later = datetime(2024, 9, 1, tzinfo=UTC)

        lot_newer = _seed_exact_lot(
            db_session, defn.id, Decimal("5"), received_at=t_later, best_before_date=same_date
        )
        lot_earlier = _seed_exact_lot(
            db_session, defn.id, Decimal("5"), received_at=t_earlier, best_before_date=same_date
        )
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]

        svc.consume_fifo(defn, Decimal("5"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot_earlier)
        db_session.refresh(lot_newer)

        assert lot_earlier.quantity == Decimal("0"), (
            "Earlier-received same-expiry lot drained first"
        )  # type: ignore[attr-defined]
        assert lot_newer.quantity == Decimal("5")  # type: ignore[attr-defined]

    def test_fefo_multi_lot_span(self, db_session: Session) -> None:
        """FEFO spanning multiple lots: near-expiry drained first, then far-expiry, then NULL."""
        defn = _seed_definition(db_session, mode="exact")

        t1 = datetime(2024, 1, 1, tzinfo=UTC)
        t2 = datetime(2024, 4, 1, tzinfo=UTC)
        t3 = datetime(2024, 7, 1, tzinfo=UTC)

        lot_null = _seed_exact_lot(
            db_session, defn.id, Decimal("3"), received_at=t1, best_before_date=None
        )
        lot_far = _seed_exact_lot(
            db_session, defn.id, Decimal("3"), received_at=t2, best_before_date=date(2025, 12, 1)
        )
        lot_near = _seed_exact_lot(
            db_session, defn.id, Decimal("3"), received_at=t3, best_before_date=date(2025, 1, 1)
        )
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]

        # Consume 5: drains lot_near (3) fully, then takes 2 from lot_far.
        touched = svc.consume_fifo(defn, Decimal("5"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot_near)
        db_session.refresh(lot_far)
        db_session.refresh(lot_null)

        assert lot_near.quantity == Decimal("0"), "Near-expiry lot must be drained first"  # type: ignore[attr-defined]
        assert lot_far.quantity == Decimal("1"), "Far-expiry lot partially drained"  # type: ignore[attr-defined]
        assert lot_null.quantity == Decimal("3"), "NULL lot must remain untouched"  # type: ignore[attr-defined]
        assert len(touched) == 2

    def test_fefo_partial_lot_decimal_precision(self, db_session: Session) -> None:
        """FEFO consume uses exact Decimal arithmetic — M2 precision invariant unbroken."""
        defn = _seed_definition(db_session, mode="exact")

        lot = _seed_exact_lot(db_session, defn.id, Decimal("10"), best_before_date=date(2025, 3, 1))
        db_session.commit()

        ctx = _make_ctx(db_session)
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]

        svc.consume_fifo(defn, Decimal("3.333333"))  # type: ignore[arg-type]
        db_session.commit()
        db_session.refresh(lot)

        assert lot.quantity == Decimal("6.666667"), "Decimal precision must be exact"  # type: ignore[attr-defined]

    def test_fefo_insufficient_stock_rejected_with_nothing_written(
        self, db_session: Session
    ) -> None:
        """Insufficient total stock: raises stock.insufficient and writes NOTHING.

        M2 transaction-integrity invariant must be unbroken under FEFO ordering.
        """
        from sqlalchemy import select as sa_select

        from app.models.stock_movement import StockMovement

        defn = _seed_definition(db_session, mode="exact")

        lot_near = _seed_exact_lot(
            db_session, defn.id, Decimal("2"), best_before_date=date(2025, 1, 1)
        )
        lot_null = _seed_exact_lot(db_session, defn.id, Decimal("1"), best_before_date=None)
        db_session.commit()

        movements_before = db_session.scalars(
            sa_select(StockMovement).where(
                StockMovement.instance_id.in_([lot_near.id, lot_null.id])  # type: ignore[attr-defined]
            )
        ).all()
        count_before = len(movements_before)

        ctx = _make_ctx(db_session)
        from app.core.errors import AppError, ErrorCode
        from app.services.stock_movement import StockMovementService

        svc = StockMovementService(db_session, ctx)  # type: ignore[arg-type]

        with pytest.raises(AppError) as exc_info:
            svc.consume_fifo(defn, Decimal("10"))  # type: ignore[arg-type]

        assert exc_info.value.code == ErrorCode.STOCK_INSUFFICIENT
        assert exc_info.value.status_code == 422

        # No new movements must have been flushed.
        movements_after = db_session.scalars(
            sa_select(StockMovement).where(
                StockMovement.instance_id.in_([lot_near.id, lot_null.id])  # type: ignore[attr-defined]
            )
        ).all()
        assert len(movements_after) == count_before, "No movements must be written on failure"

        # Both lot quantities must be unchanged.
        db_session.refresh(lot_near)
        db_session.refresh(lot_null)
        assert lot_near.quantity == Decimal("2")  # type: ignore[attr-defined]
        assert lot_null.quantity == Decimal("1")  # type: ignore[attr-defined]
