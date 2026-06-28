"""M2 Step 2 tests: stock movement ledger table and repository.

Required coverage (per M2.md §9 Step 2 / §10 blind-review points):

Migration:
- Migration 0011 upgrades cleanly (table + all indexes present).
- Migration 0011 downgrades cleanly (table + indexes removed).
- Full upgrade→downgrade roundtrip leaves the DB clean.

Repository:
- ``append`` inserts a row; ``get`` retrieves it by PK.
- ``list_for_instance`` returns rows newest-first by (occurred_at DESC, id DESC).
- ``sum_delta_for_instance`` returns the correct Decimal sum (mixed signs).
- ``sum_delta_for_instance`` returns Decimal("0") when there are no movements.
- ``find_reversal_of`` returns the reversal row when it exists, None when not.
- ``delete_for_instance`` removes only that lot's rows (other lots untouched).
- **No** ``update`` method on the repository (append-only).

Partial-unique enforcement:
- Inserting a second movement with the same non-null ``reverses_movement_id``
  raises IntegrityError (DB-level partial-unique).
- Two movements with ``reverses_movement_id IS NULL`` are both allowed.

CASCADE:
- Deleting a ``StockInstance`` row (with PRAGMA foreign_keys=ON) deletes its
  movements via the CASCADE FK.

Constant:
- ``MOVEMENT_TYPES`` is defined in ``app.core.stock`` with the six expected values.
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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m2step2_")
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
    """In-memory SQLite session with all models registered, FK enforcement ON."""
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

    # Seed item_kinds so definition creation via service works.
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


def _seed_instance(session: Session) -> int:
    """Insert a minimal StockInstance and return its id."""
    from app.models.item_definition import ItemDefinition
    from app.models.item_kind import ItemKind
    from app.models.stock_instance import StockInstance

    kind = session.scalars(
        __import__("sqlalchemy", fromlist=["select"])
        .select(ItemKind)
        .where(ItemKind.code == "consumable")
    ).first()
    assert kind is not None

    defn = ItemDefinition(name="Test Widget", unit="pcs", kind_id=kind.id)
    session.add(defn)
    session.flush()

    inst = StockInstance(definition_id=defn.id, quantity=Decimal("10"))
    session.add(inst)
    session.flush()
    return inst.id


# ---------------------------------------------------------------------------
# 1. MOVEMENT_TYPES constant
# ---------------------------------------------------------------------------


class TestMovementTypesConstant:
    """MOVEMENT_TYPES constant is present and correct."""

    def test_movement_types_defined(self) -> None:
        """MOVEMENT_TYPES is importable from app.core.stock."""
        from app.core.stock import MOVEMENT_TYPES

        assert MOVEMENT_TYPES is not None

    def test_movement_types_contains_all_six(self) -> None:
        """MOVEMENT_TYPES contains exactly the six required values."""
        from app.core.stock import MOVEMENT_TYPES

        assert set(MOVEMENT_TYPES) == {
            "intake",
            "consume",
            "move",
            "adjust",
            "discard",
            "correction",
        }

    def test_movement_types_is_tuple(self) -> None:
        """MOVEMENT_TYPES is a tuple (immutable constant)."""
        from app.core.stock import MOVEMENT_TYPES

        assert isinstance(MOVEMENT_TYPES, tuple)


# ---------------------------------------------------------------------------
# 2. Repository: append + get
# ---------------------------------------------------------------------------


class TestRepositoryAppendAndGet:
    """StockMovementRepository.append inserts a row; get retrieves it."""

    def test_append_returns_movement_with_id(self, db_session: Session) -> None:
        """append() flushes and returns a row with a PK assigned."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        m = repo.append(
            instance_id=instance_id,
            type="intake",
            quantity_delta=Decimal("5"),
        )
        assert m.id is not None
        assert m.instance_id == instance_id
        assert m.type == "intake"
        assert m.quantity_delta == Decimal("5")

    def test_get_returns_inserted_row(self, db_session: Session) -> None:
        """get(id) returns the same row that append() created."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        m = repo.append(instance_id=instance_id, type="intake", quantity_delta=Decimal("3"))
        db_session.commit()

        retrieved = repo.get(m.id)
        assert retrieved is not None
        assert retrieved.id == m.id
        assert retrieved.quantity_delta == Decimal("3")

    def test_get_returns_none_for_missing(self, db_session: Session) -> None:
        """get() returns None when the PK does not exist."""
        from app.repositories.stock_movement import StockMovementRepository

        repo = StockMovementRepository(db_session)
        assert repo.get(99999) is None

    def test_append_stores_optional_fields(self, db_session: Session) -> None:
        """append() persists note, user_id, from/to_location_id."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        m = repo.append(
            instance_id=instance_id,
            type="move",
            quantity_delta=Decimal("0"),
            from_location_id=None,
            to_location_id=None,
            note="test note",
            user_id=None,
        )
        assert m.note == "test note"


# ---------------------------------------------------------------------------
# 3. Repository: list_for_instance (order: newest-first)
# ---------------------------------------------------------------------------


class TestListForInstance:
    """list_for_instance returns history newest-first."""

    def test_list_empty_when_no_movements(self, db_session: Session) -> None:
        """Returns empty list when the lot has no movements."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        assert repo.list_for_instance(instance_id) == []

    def test_list_returns_all_movements(self, db_session: Session) -> None:
        """Returns all movements for the given lot."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        repo.append(instance_id=instance_id, type="intake", quantity_delta=Decimal("10"))
        repo.append(instance_id=instance_id, type="consume", quantity_delta=Decimal("-3"))
        db_session.commit()

        rows = repo.list_for_instance(instance_id)
        assert len(rows) == 2

    def test_list_newest_first_by_id_when_same_timestamp(self, db_session: Session) -> None:
        """Rows inserted in sequence appear id-DESC when occurred_at is equal."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)

        # Insert multiple rows without an explicit occurred_at so they share
        # the same server_default timestamp (same second in SQLite).
        m1 = repo.append(instance_id=instance_id, type="intake", quantity_delta=Decimal("10"))
        m2 = repo.append(instance_id=instance_id, type="consume", quantity_delta=Decimal("-3"))
        m3 = repo.append(instance_id=instance_id, type="adjust", quantity_delta=Decimal("2"))
        db_session.commit()

        rows = repo.list_for_instance(instance_id)
        ids = [r.id for r in rows]
        # Expect descending id order when occurred_at is the same.
        assert ids == sorted(ids, reverse=True)
        assert set(ids) == {m1.id, m2.id, m3.id}

    def test_list_isolates_by_instance(self, db_session: Session) -> None:
        """list_for_instance returns only rows for the specified lot."""
        from app.repositories.stock_movement import StockMovementRepository

        inst_a = _seed_instance(db_session)
        inst_b = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        repo.append(instance_id=inst_a, type="intake", quantity_delta=Decimal("5"))
        repo.append(instance_id=inst_b, type="intake", quantity_delta=Decimal("7"))
        db_session.commit()

        rows_a = repo.list_for_instance(inst_a)
        rows_b = repo.list_for_instance(inst_b)
        assert len(rows_a) == 1
        assert rows_a[0].quantity_delta == Decimal("5")
        assert len(rows_b) == 1
        assert rows_b[0].quantity_delta == Decimal("7")


# ---------------------------------------------------------------------------
# 4. Repository: sum_delta_for_instance
# ---------------------------------------------------------------------------


class TestSumDeltaForInstance:
    """sum_delta_for_instance returns the correct Decimal aggregate."""

    def test_sum_zero_when_no_movements(self, db_session: Session) -> None:
        """Returns Decimal('0') when the lot has no movements at all."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        total = repo.sum_delta_for_instance(instance_id)
        assert total == Decimal("0")
        assert isinstance(total, Decimal)

    def test_sum_single_intake(self, db_session: Session) -> None:
        """Single intake returns the intake amount."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        repo.append(instance_id=instance_id, type="intake", quantity_delta=Decimal("10"))
        db_session.commit()

        assert repo.sum_delta_for_instance(instance_id) == Decimal("10")

    def test_sum_mixed_signs(self, db_session: Session) -> None:
        """Mixed-sign deltas sum correctly."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        repo.append(instance_id=instance_id, type="intake", quantity_delta=Decimal("10"))
        repo.append(instance_id=instance_id, type="consume", quantity_delta=Decimal("-3"))
        repo.append(instance_id=instance_id, type="adjust", quantity_delta=Decimal("1"))
        db_session.commit()

        # 10 - 3 + 1 = 8
        assert repo.sum_delta_for_instance(instance_id) == Decimal("8")

    def test_sum_decimal_precision(self, db_session: Session) -> None:
        """Fractional quantities are summed precisely (no float rounding)."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        repo.append(instance_id=instance_id, type="intake", quantity_delta=Decimal("1.1"))
        repo.append(instance_id=instance_id, type="intake", quantity_delta=Decimal("2.2"))
        db_session.commit()

        result = repo.sum_delta_for_instance(instance_id)
        assert result == Decimal("3.3")
        assert isinstance(result, Decimal)

    def test_sum_returns_decimal_type(self, db_session: Session) -> None:
        """Return type is always Decimal, never int or float."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        total = repo.sum_delta_for_instance(instance_id)
        assert isinstance(total, Decimal)


# ---------------------------------------------------------------------------
# 5. Repository: find_reversal_of
# ---------------------------------------------------------------------------


class TestFindReversalOf:
    """find_reversal_of returns the reversal row or None."""

    def test_returns_none_when_no_reversal(self, db_session: Session) -> None:
        """Returns None when no movement reverses the given id."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        m = repo.append(instance_id=instance_id, type="intake", quantity_delta=Decimal("5"))
        db_session.commit()

        assert repo.find_reversal_of(m.id) is None

    def test_returns_reversal_when_exists(self, db_session: Session) -> None:
        """Returns the correction row when one exists."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        original = repo.append(instance_id=instance_id, type="intake", quantity_delta=Decimal("5"))
        reversal = repo.append(
            instance_id=instance_id,
            type="correction",
            quantity_delta=Decimal("-5"),
            reverses_movement_id=original.id,
        )
        db_session.commit()

        found = repo.find_reversal_of(original.id)
        assert found is not None
        assert found.id == reversal.id
        assert found.reverses_movement_id == original.id

    def test_returns_none_for_nonexistent_movement(self, db_session: Session) -> None:
        """Returns None for a movement id that does not exist."""
        from app.repositories.stock_movement import StockMovementRepository

        repo = StockMovementRepository(db_session)
        assert repo.find_reversal_of(99999) is None


# ---------------------------------------------------------------------------
# 6. Repository: delete_for_instance
# ---------------------------------------------------------------------------


class TestDeleteForInstance:
    """delete_for_instance removes only that lot's rows."""

    def test_deletes_all_movements_for_instance(self, db_session: Session) -> None:
        """After delete_for_instance, list_for_instance returns empty."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        repo.append(instance_id=instance_id, type="intake", quantity_delta=Decimal("10"))
        repo.append(instance_id=instance_id, type="consume", quantity_delta=Decimal("-3"))
        db_session.commit()

        repo.delete_for_instance(instance_id)
        db_session.commit()

        assert repo.list_for_instance(instance_id) == []

    def test_does_not_delete_other_instance_movements(self, db_session: Session) -> None:
        """delete_for_instance only removes rows for the specified lot."""
        from app.repositories.stock_movement import StockMovementRepository

        inst_a = _seed_instance(db_session)
        inst_b = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        repo.append(instance_id=inst_a, type="intake", quantity_delta=Decimal("5"))
        m_b = repo.append(instance_id=inst_b, type="intake", quantity_delta=Decimal("7"))
        db_session.commit()

        repo.delete_for_instance(inst_a)
        db_session.commit()

        assert repo.list_for_instance(inst_a) == []
        rows_b = repo.list_for_instance(inst_b)
        assert len(rows_b) == 1
        assert rows_b[0].id == m_b.id

    def test_delete_noop_when_no_movements(self, db_session: Session) -> None:
        """delete_for_instance on a lot with no movements is a no-op."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        # Should not raise.
        repo.delete_for_instance(instance_id)
        db_session.commit()


# ---------------------------------------------------------------------------
# 7. No update method (append-only ledger)
# ---------------------------------------------------------------------------


class TestAppendOnly:
    """The repository must NOT have an update method."""

    def test_no_update_method(self) -> None:
        """StockMovementRepository has no update method (ledger is append-only)."""
        from app.repositories.stock_movement import StockMovementRepository

        assert not hasattr(StockMovementRepository, "update"), (
            "StockMovementRepository must not have an update() method — "
            "the ledger is append-only (M2 §2)."
        )


# ---------------------------------------------------------------------------
# 8. Partial-unique enforcement at the DB level
# ---------------------------------------------------------------------------


class TestPartialUniqueConstraint:
    """DB-level partial-unique on reverses_movement_id enforces at-most-once reversal."""

    def test_two_null_reverses_id_allowed(self, db_session: Session) -> None:
        """Two movements with reverses_movement_id=NULL are both accepted."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        repo.append(instance_id=instance_id, type="intake", quantity_delta=Decimal("10"))
        repo.append(instance_id=instance_id, type="consume", quantity_delta=Decimal("-3"))
        # Both have reverses_movement_id=None — must not raise.
        db_session.commit()

        rows = repo.list_for_instance(instance_id)
        assert len(rows) == 2

    def test_second_reversal_of_same_movement_raises_integrity_error(
        self, db_session: Session
    ) -> None:
        """Inserting two movements with the same non-null reverses_movement_id raises IntegrityError."""
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        original = repo.append(instance_id=instance_id, type="intake", quantity_delta=Decimal("5"))
        # First reversal — should succeed.
        repo.append(
            instance_id=instance_id,
            type="correction",
            quantity_delta=Decimal("-5"),
            reverses_movement_id=original.id,
        )
        db_session.commit()

        # Second reversal of the SAME original — must violate the partial-unique.
        with pytest.raises(IntegrityError):
            repo.append(
                instance_id=instance_id,
                type="correction",
                quantity_delta=Decimal("-5"),
                reverses_movement_id=original.id,
            )
            db_session.commit()

        db_session.rollback()


# ---------------------------------------------------------------------------
# 9. CASCADE: deleting a StockInstance deletes its movements
# ---------------------------------------------------------------------------


class TestCascadeOnInstanceDelete:
    """Deleting a StockInstance cascade-deletes its movements (FK ondelete=CASCADE)."""

    def test_delete_instance_removes_movements(self, db_session: Session) -> None:
        """After deleting the parent instance, its movements no longer exist."""
        from sqlalchemy import select

        from app.models.stock_instance import StockInstance
        from app.models.stock_movement import StockMovement
        from app.repositories.stock_movement import StockMovementRepository

        instance_id = _seed_instance(db_session)
        repo = StockMovementRepository(db_session)
        repo.append(instance_id=instance_id, type="intake", quantity_delta=Decimal("10"))
        repo.append(instance_id=instance_id, type="consume", quantity_delta=Decimal("-3"))
        db_session.commit()

        # Verify movements exist before deletion.
        before = db_session.scalars(
            select(StockMovement).where(StockMovement.instance_id == instance_id)
        ).all()
        assert len(before) == 2

        # Delete the parent instance.
        instance = db_session.get(StockInstance, instance_id)
        assert instance is not None
        db_session.delete(instance)
        db_session.commit()

        # Movements should be gone.
        after = db_session.scalars(
            select(StockMovement).where(StockMovement.instance_id == instance_id)
        ).all()
        assert after == []


# ---------------------------------------------------------------------------
# 10. Alembic migration 0011
# ---------------------------------------------------------------------------


class TestAlembicMigration0011:
    """Migration 0011 must upgrade and downgrade cleanly."""

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

    def test_upgrade_0011_creates_table(self) -> None:
        """Upgrading to 0011 creates the stock_movements table."""
        from sqlalchemy import create_engine

        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "0011", url=url)
            assert rc == 0, f"alembic upgrade 0011 failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "stock_movements" in table_names, (
                    f"stock_movements table not found; tables: {table_names}"
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0011_columns_present(self) -> None:
        """Upgrading to 0011 creates all required columns in stock_movements."""
        from sqlalchemy import create_engine

        url, db_path = _make_temp_db_url()
        expected_columns = {
            "id",
            "instance_id",
            "type",
            "quantity_delta",
            "from_location_id",
            "to_location_id",
            "occurred_at",
            "note",
            "reverses_movement_id",
            "user_id",
            "created_at",
        }
        try:
            rc, out = self._run_alembic("upgrade", "0011", url=url)
            assert rc == 0, f"alembic upgrade 0011 failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols = conn.execute(text("PRAGMA table_info(stock_movements)")).fetchall()
                col_names = {row[1] for row in cols}
                missing = expected_columns - col_names
                assert not missing, f"Missing columns in stock_movements: {missing}"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0011_indexes_present(self) -> None:
        """Upgrading to 0011 creates the three required indexes."""
        from sqlalchemy import create_engine

        url, db_path = _make_temp_db_url()
        expected_indexes = {
            "ix_stock_movements_instance_id",
            "ix_stock_movements_instance_occurred",
            "uq_stock_movements_reversal",
        }
        try:
            rc, out = self._run_alembic("upgrade", "0011", url=url)
            assert rc == 0, f"alembic upgrade 0011 failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                idx = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='stock_movements'"
                    )
                ).fetchall()
                idx_names = {row[0] for row in idx}
                missing = expected_indexes - idx_names
                assert not missing, (
                    f"Missing indexes on stock_movements: {missing}; found: {idx_names}"
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_0011_drops_table(self) -> None:
        """Downgrading from 0011 to 0010 removes the stock_movements table."""
        from sqlalchemy import create_engine

        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "0011", url=url)
            assert rc_up == 0, f"upgrade 0011 failed:\n{out_up}"

            rc_dn, out_dn = self._run_alembic("downgrade", "0010", url=url)
            assert rc_dn == 0, f"downgrade to 0010 failed:\n{out_dn}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "stock_movements" not in table_names, (
                    "stock_movements still present after downgrade to 0010"
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_full_upgrade_downgrade_roundtrip(self) -> None:
        """Full upgrade to head then downgrade to base leaves no application tables."""
        from sqlalchemy import create_engine

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

    def test_stepwise_upgrade_0010_to_0011(self) -> None:
        """Stepwise upgrade from 0010 to 0011 succeeds."""
        from sqlalchemy import create_engine

        url, db_path = _make_temp_db_url()
        try:
            rc10, out10 = self._run_alembic("upgrade", "0010", url=url)
            assert rc10 == 0, f"upgrade 0010 failed:\n{out10}"

            rc11, out11 = self._run_alembic("upgrade", "0011", url=url)
            assert rc11 == 0, f"upgrade 0011 failed:\n{out11}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "stock_movements" in table_names
        finally:
            if db_path.exists():
                db_path.unlink()
