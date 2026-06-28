"""M3 Step 2 tests: per-lot best_before_date and auto-compute on intake.

Required coverage (per M3.md §5 "Backend" + §9 Step 2 "Tests" + §10 blind-review
checkpoints):

Auto-compute precedence (the easy-to-get-wrong date math — §4.2):
- omitted best_before_date + definition default N ⇒ result == today + N;
- explicit date wins (stored verbatim) even when a default exists;
- explicit past date wins (stored verbatim, even when a default exists);
- explicit None stays NULL even when a default exists;
- omitted + no default ⇒ NULL;
- works for all three modes (exact / level / none);
- non-retroactive: changing definition's default_best_before_days does NOT
  alter an existing lot's stored best_before_date;
- subsequent intake into an existing lot leaves its best_before_date unchanged.

PATCH set/clear:
- PATCH can set a best_before_date;
- PATCH can clear it to NULL (omit-vs-clear distinction via model_fields_set).

Migration 0014:
- upgrade on a DB at 0013 adds best_before_date column;
- downgrade cleanly removes it.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_temp_db_url() -> tuple[str, Path]:
    """Return a (url, path) pair for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m3step2_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()  # Start empty.
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
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite; sets SECRET_KEY, ENVIRONMENT=test, DATABASE_URL."""
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-m3-step2")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def test_client(temp_db: Path) -> Generator[TestClient]:  # noqa: ARG001
    """TestClient with a temp-file SQLite, full schema, and an authenticated session."""
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
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as sm_mod
    import app.models.user as user_mod

    importlib.reload(db_base_mod)
    importlib.reload(hh_mod)
    importlib.reload(user_mod)
    importlib.reload(sess_mod)
    importlib.reload(app_config_mod)
    importlib.reload(stock_instance_mod)
    importlib.reload(loc_mod)
    importlib.reload(cat_mod)
    importlib.reload(ikind_mod)
    importlib.reload(idef_mod)
    importlib.reload(sm_mod)
    importlib.reload(audit_log_mod)

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

            # Seed the three system kinds.
            for code, name in [
                ("durable", "Durable"),
                ("consumable", "Consumable"),
                ("perishable", "Perishable"),
            ]:
                db.add(ItemKind(code=code, name=name, is_system=True))
            db.commit()
        finally:
            db.close()

        response = client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "adminpass"},
        )
        assert response.status_code == 200
        yield client

    drop_all_sqlite(Base, engine)


@pytest.fixture()
def db_session() -> Generator[Session]:
    """Fresh in-memory SQLite session with all models registered and kinds seeded."""
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
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as sm_mod
    import app.models.user as user_mod

    importlib.reload(db_base_mod)
    importlib.reload(hh_mod)
    importlib.reload(user_mod)
    importlib.reload(sess_mod)
    importlib.reload(app_config_mod)
    importlib.reload(cat_mod)
    importlib.reload(ikind_mod)
    importlib.reload(idef_mod)
    importlib.reload(stock_instance_mod)
    importlib.reload(sm_mod)
    importlib.reload(loc_mod)
    importlib.reload(audit_log_mod)

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

    # Seed item_kinds so service default-kind resolution works.
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


# ---------------------------------------------------------------------------
# HTTP helper functions
# ---------------------------------------------------------------------------


def _create_definition(
    client: TestClient,
    name: str = "Test Item",
    **kwargs: object,
) -> dict:  # type: ignore[type-arg]
    """POST /api/definitions and return the JSON dict."""
    payload: dict = {"name": name, **kwargs}  # type: ignore[type-arg]
    resp = client.post("/api/definitions", json=payload)
    assert resp.status_code == 201, f"create_definition failed: {resp.status_code} {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_instance(
    client: TestClient,
    definition_id: int,
    *,
    expect_status: int = 201,
    **kwargs: object,
) -> dict:  # type: ignore[type-arg]
    """POST /api/instances and return the JSON dict."""
    payload: dict = {"definition_id": definition_id, **kwargs}  # type: ignore[type-arg]
    resp = client.post("/api/instances", json=payload)
    assert resp.status_code == expect_status, (
        f"create_instance failed: {resp.status_code} {resp.json()}"
    )
    return resp.json()  # type: ignore[return-value]


def _seed_definition(
    session: Session,
    *,
    mode: str = "exact",
    name: str | None = None,
    default_best_before_days: int | None = None,
) -> object:
    """Seed a definition with the given tracking mode and optional shelf-life default."""
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
        default_best_before_days=default_best_before_days,
    )
    session.add(defn)
    session.flush()
    return defn


# ---------------------------------------------------------------------------
# 1. Schema: InstanceResponse now carries best_before_date
# ---------------------------------------------------------------------------


class TestInstanceResponseSchema:
    """InstanceResponse/InstanceCreate/InstanceUpdate carry best_before_date."""

    def test_response_has_best_before_date_field(self) -> None:
        """InstanceResponse.model_fields contains best_before_date."""
        from app.schemas.stock_instance import InstanceResponse

        assert "best_before_date" in InstanceResponse.model_fields

    def test_create_has_best_before_date_field(self) -> None:
        """InstanceCreate.model_fields contains best_before_date."""
        from app.schemas.stock_instance import InstanceCreate

        assert "best_before_date" in InstanceCreate.model_fields

    def test_update_has_best_before_date_field(self) -> None:
        """InstanceUpdate.model_fields contains best_before_date."""
        from app.schemas.stock_instance import InstanceUpdate

        assert "best_before_date" in InstanceUpdate.model_fields

    def test_create_best_before_date_not_in_model_fields_set_when_omitted(self) -> None:
        """InstanceCreate without best_before_date: 'best_before_date' NOT in model_fields_set."""
        from app.schemas.stock_instance import InstanceCreate

        data = InstanceCreate(definition_id=1)
        assert "best_before_date" not in data.model_fields_set

    def test_create_best_before_date_in_model_fields_set_when_provided(self) -> None:
        """InstanceCreate with explicit best_before_date: 'best_before_date' IN model_fields_set."""
        from app.schemas.stock_instance import InstanceCreate

        data = InstanceCreate(definition_id=1, best_before_date=date.today())
        assert "best_before_date" in data.model_fields_set

    def test_create_explicit_none_in_model_fields_set(self) -> None:
        """InstanceCreate with explicit None: 'best_before_date' IN model_fields_set."""
        from app.schemas.stock_instance import InstanceCreate

        data = InstanceCreate(definition_id=1, best_before_date=None)
        assert "best_before_date" in data.model_fields_set

    def test_update_best_before_date_not_in_model_fields_set_when_omitted(self) -> None:
        """InstanceUpdate without best_before_date: not in model_fields_set."""
        from app.schemas.stock_instance import InstanceUpdate

        data = InstanceUpdate()
        assert "best_before_date" not in data.model_fields_set

    def test_update_explicit_none_in_model_fields_set(self) -> None:
        """InstanceUpdate with explicit None: in model_fields_set."""
        from app.schemas.stock_instance import InstanceUpdate

        data = InstanceUpdate(best_before_date=None)
        assert "best_before_date" in data.model_fields_set


# ---------------------------------------------------------------------------
# 2. Auto-compute precedence (service unit tests)
# ---------------------------------------------------------------------------


class TestAutoComputePrecedence:
    """StockInstanceService._resolve_best_before precedence (§4.2)."""

    def test_omitted_with_default_computes_today_plus_n(self, db_session: Session) -> None:
        """Omitted best_before_date + definition default N ⇒ today + N."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact", default_best_before_days=7)
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("1")))
        db_session.commit()
        db_session.refresh(inst)

        expected = date.today() + timedelta(days=7)
        assert inst.best_before_date == expected

    def test_omitted_with_zero_default_computes_today(self, db_session: Session) -> None:
        """Omitted best_before_date + default 0 ⇒ today (same-day expiry)."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact", default_best_before_days=0)
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("1")))
        db_session.commit()
        db_session.refresh(inst)

        assert inst.best_before_date == date.today()

    def test_omitted_with_no_default_gives_null(self, db_session: Session) -> None:
        """Omitted best_before_date + no default ⇒ NULL."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact", default_best_before_days=None)
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("1")))
        db_session.commit()
        db_session.refresh(inst)

        assert inst.best_before_date is None

    def test_explicit_future_date_wins_over_default(self, db_session: Session) -> None:
        """Explicit best_before_date wins even when a default exists."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact", default_best_before_days=30)
        explicit_date = date.today() + timedelta(days=5)
        svc = StockInstanceService(db_session)
        inst = svc.create(
            InstanceCreate(
                definition_id=defn.id,
                quantity=Decimal("1"),
                best_before_date=explicit_date,
            )
        )
        db_session.commit()
        db_session.refresh(inst)

        # Explicit date wins — NOT today + 30.
        assert inst.best_before_date == explicit_date

    def test_explicit_past_date_wins_over_default(self, db_session: Session) -> None:
        """Explicit past best_before_date wins — stored verbatim even when already expired."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact", default_best_before_days=30)
        past_date = date.today() - timedelta(days=10)
        svc = StockInstanceService(db_session)
        inst = svc.create(
            InstanceCreate(
                definition_id=defn.id,
                quantity=Decimal("1"),
                best_before_date=past_date,
            )
        )
        db_session.commit()
        db_session.refresh(inst)

        assert inst.best_before_date == past_date

    def test_explicit_none_wins_over_default(self, db_session: Session) -> None:
        """Explicit None stays NULL even when a definition default exists."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact", default_best_before_days=7)
        svc = StockInstanceService(db_session)
        # Explicitly pass best_before_date=None — it must be in model_fields_set.
        data = InstanceCreate(definition_id=defn.id, quantity=Decimal("1"), best_before_date=None)
        assert "best_before_date" in data.model_fields_set
        inst = svc.create(data)
        db_session.commit()
        db_session.refresh(inst)

        # Explicit None overrides the default → stays NULL.
        assert inst.best_before_date is None

    # ── Three mode variants ──────────────────────────────────────────────────

    def test_exact_mode_auto_compute(self, db_session: Session) -> None:
        """exact mode: omitted + default ⇒ today + N."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact", default_best_before_days=14)
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("3")))
        db_session.commit()
        db_session.refresh(inst)

        assert inst.best_before_date == date.today() + timedelta(days=14)

    def test_level_mode_auto_compute(self, db_session: Session) -> None:
        """level mode: omitted + default ⇒ today + N."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="level", default_best_before_days=14)
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, stock_level="high"))
        db_session.commit()
        db_session.refresh(inst)

        assert inst.best_before_date == date.today() + timedelta(days=14)

    def test_none_mode_auto_compute(self, db_session: Session) -> None:
        """none mode: omitted + default ⇒ today + N."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="none", default_best_before_days=14)
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id))
        db_session.commit()
        db_session.refresh(inst)

        assert inst.best_before_date == date.today() + timedelta(days=14)

    def test_exact_mode_no_default_null(self, db_session: Session) -> None:
        """exact mode: omitted + no default ⇒ NULL."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact", default_best_before_days=None)
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("1")))
        db_session.commit()
        db_session.refresh(inst)

        assert inst.best_before_date is None

    def test_level_mode_no_default_null(self, db_session: Session) -> None:
        """level mode: omitted + no default ⇒ NULL."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="level", default_best_before_days=None)
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, stock_level="medium"))
        db_session.commit()
        db_session.refresh(inst)

        assert inst.best_before_date is None

    def test_none_mode_no_default_null(self, db_session: Session) -> None:
        """none mode: omitted + no default ⇒ NULL."""
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="none", default_best_before_days=None)
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id))
        db_session.commit()
        db_session.refresh(inst)

        assert inst.best_before_date is None


# ---------------------------------------------------------------------------
# 3. Non-retroactive: changing definition's default does NOT alter existing lots
# ---------------------------------------------------------------------------


class TestNonRetroactive:
    """Changing default_best_before_days on a populated definition never rewrites
    existing lots' best_before_date.  (M3 §2 locked decision.)"""

    def test_changing_definition_default_does_not_alter_existing_lot(
        self, db_session: Session
    ) -> None:
        """Creating a lot, then changing the definition default → lot unchanged."""
        from app.repositories.item_definition import ItemDefinitionRepository
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        # Create definition with default 7 days.
        defn = _seed_definition(db_session, mode="exact", default_best_before_days=7)
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("1")))
        db_session.commit()
        db_session.refresh(inst)

        original_date = inst.best_before_date
        assert original_date == date.today() + timedelta(days=7)

        # Now change the definition's default to 30 days.
        def_repo = ItemDefinitionRepository(db_session)
        def_repo.update(defn, set_default_best_before_days=True, default_best_before_days=30)
        db_session.commit()

        # The existing lot must NOT have changed.
        db_session.expire(inst)
        assert inst.best_before_date == original_date

    def test_subsequent_lot_uses_new_default(self, db_session: Session) -> None:
        """After changing the definition default, a NEW lot uses the new default."""
        from app.repositories.item_definition import ItemDefinitionRepository
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact", default_best_before_days=7)
        svc = StockInstanceService(db_session)
        # Create the first lot under the old default.
        inst1 = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("1")))
        db_session.commit()

        # Change the default.
        def_repo = ItemDefinitionRepository(db_session)
        def_repo.update(defn, set_default_best_before_days=True, default_best_before_days=60)
        db_session.commit()

        # Create a second lot — it must use the new 60-day default.
        inst2 = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("2")))
        db_session.commit()
        db_session.refresh(inst2)

        assert inst2.best_before_date == date.today() + timedelta(days=60)
        # First lot is still the original date.
        db_session.refresh(inst1)
        assert inst1.best_before_date == date.today() + timedelta(days=7)


# ---------------------------------------------------------------------------
# 4. Subsequent intake into existing lot leaves best_before_date unchanged
# ---------------------------------------------------------------------------


class TestSubsequentIntakeNonRetroactive:
    """A subsequent intake (movement) into an existing lot does NOT touch its
    best_before_date.  Auto-compute lives only in instance create, never in the
    movement layer.  (M3 §4.1 / §9 Step 2 design constraint.)"""

    def test_intake_movement_does_not_change_best_before_date(self, db_session: Session) -> None:
        """POST /instances/{id}/intake leaves best_before_date unchanged."""
        from app.core.context import RequestContext
        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService
        from app.services.stock_movement import StockMovementService

        defn = _seed_definition(db_session, mode="exact", default_best_before_days=7)
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("5")))
        db_session.commit()
        db_session.refresh(inst)

        stored_date = inst.best_before_date
        assert stored_date is not None  # auto-computed to today + 7

        # Simulate a subsequent intake (StockMovementService.intake).
        from app.models.household import Household
        from app.models.user import User

        # Need a user + household for StockMovementService context.
        user = User(email="test@example.com", password_hash="hash")
        db_session.add(user)
        db_session.flush()
        hh = Household(name="Test HH")
        db_session.add(hh)
        db_session.flush()

        ctx = RequestContext(household=hh, user=user)  # type: ignore[arg-type]
        movement_svc = StockMovementService(db_session, ctx)
        movement_svc.intake(inst, Decimal("3"))
        db_session.commit()
        db_session.refresh(inst)

        # Quantity updated via ledger, but best_before_date must not change.
        assert inst.quantity == Decimal("8")
        assert inst.best_before_date == stored_date


# ---------------------------------------------------------------------------
# 5. PATCH set / clear (omit-vs-clear distinction)
# ---------------------------------------------------------------------------


class TestPatchBestBeforeDate:
    """PATCH /instances/{id} can set and clear best_before_date."""

    def test_patch_sets_best_before_date(self, db_session: Session) -> None:
        """PATCH with best_before_date sets it on an existing lot."""
        from app.schemas.stock_instance import InstanceCreate, InstanceUpdate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact", default_best_before_days=None)
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("1")))
        db_session.commit()
        db_session.refresh(inst)
        assert inst.best_before_date is None

        new_date = date.today() + timedelta(days=10)
        updated = svc.update(inst.id, InstanceUpdate(best_before_date=new_date))
        db_session.commit()
        db_session.refresh(updated)

        assert updated.best_before_date == new_date

    def test_patch_clears_best_before_date_to_null(self, db_session: Session) -> None:
        """PATCH with explicit best_before_date=None clears it to NULL."""
        from app.schemas.stock_instance import InstanceCreate, InstanceUpdate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact", default_best_before_days=7)
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("1")))
        db_session.commit()
        db_session.refresh(inst)
        assert inst.best_before_date is not None  # auto-computed

        # PATCH with explicit None must clear it.
        update_data = InstanceUpdate(best_before_date=None)
        assert "best_before_date" in update_data.model_fields_set
        updated = svc.update(inst.id, update_data)
        db_session.commit()
        db_session.refresh(updated)

        assert updated.best_before_date is None

    def test_patch_omitting_best_before_date_leaves_it_unchanged(self, db_session: Session) -> None:
        """PATCH without best_before_date in body leaves the stored date intact."""
        from app.schemas.stock_instance import InstanceCreate, InstanceUpdate
        from app.services.stock_instance import StockInstanceService

        defn = _seed_definition(db_session, mode="exact", default_best_before_days=7)
        svc = StockInstanceService(db_session)
        inst = svc.create(InstanceCreate(definition_id=defn.id, quantity=Decimal("1")))
        db_session.commit()
        db_session.refresh(inst)
        original_date = inst.best_before_date
        assert original_date is not None

        # PATCH only the serial — best_before_date must not change.
        update_data = InstanceUpdate(serial="SN-PATCH-TEST")
        assert "best_before_date" not in update_data.model_fields_set
        updated = svc.update(inst.id, update_data)
        db_session.commit()
        db_session.refresh(updated)

        assert updated.best_before_date == original_date
        assert updated.serial == "SN-PATCH-TEST"


# ---------------------------------------------------------------------------
# 6. Repository: create / update thread best_before_date
# ---------------------------------------------------------------------------


class TestRepositoryBestBeforeDate:
    """StockInstanceRepository.create/update thread best_before_date correctly."""

    def test_repo_create_stores_best_before_date(self, db_session: Session) -> None:
        """Repository.create persists best_before_date."""
        from sqlalchemy import select

        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.repositories.stock_instance import StockInstanceRepository

        kind = db_session.scalars(select(ItemKind).where(ItemKind.code == "consumable")).first()
        assert kind is not None
        defn = ItemDefinition(
            name="Repo-BB-Test", unit="pcs", kind_id=kind.id, stock_tracking_mode="none"
        )
        db_session.add(defn)
        db_session.flush()

        target_date = date.today() + timedelta(days=5)
        repo = StockInstanceRepository(db_session)
        inst = repo.create(definition_id=defn.id, best_before_date=target_date)
        db_session.commit()
        db_session.expire(inst)

        assert inst.best_before_date == target_date

    def test_repo_create_none_best_before_date(self, db_session: Session) -> None:
        """Repository.create with best_before_date=None stores NULL."""
        from sqlalchemy import select

        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.repositories.stock_instance import StockInstanceRepository

        kind = db_session.scalars(select(ItemKind).where(ItemKind.code == "consumable")).first()
        assert kind is not None
        defn = ItemDefinition(
            name="Repo-BB-Null", unit="pcs", kind_id=kind.id, stock_tracking_mode="none"
        )
        db_session.add(defn)
        db_session.flush()

        repo = StockInstanceRepository(db_session)
        inst = repo.create(definition_id=defn.id, best_before_date=None)
        db_session.commit()
        db_session.expire(inst)

        assert inst.best_before_date is None

    def test_repo_update_set_best_before_date(self, db_session: Session) -> None:
        """Repository.update with set_best_before_date=True sets the date."""
        from sqlalchemy import select

        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.repositories.stock_instance import StockInstanceRepository

        kind = db_session.scalars(select(ItemKind).where(ItemKind.code == "consumable")).first()
        assert kind is not None
        defn = ItemDefinition(
            name="Repo-BB-Update", unit="pcs", kind_id=kind.id, stock_tracking_mode="none"
        )
        db_session.add(defn)
        db_session.flush()

        repo = StockInstanceRepository(db_session)
        inst = repo.create(definition_id=defn.id, best_before_date=None)
        db_session.commit()

        new_date = date.today() + timedelta(days=20)
        repo.update(inst, set_best_before_date=True, best_before_date=new_date)
        db_session.commit()
        db_session.expire(inst)

        assert inst.best_before_date == new_date

    def test_repo_update_clear_best_before_date_to_null(self, db_session: Session) -> None:
        """Repository.update with set_best_before_date=True and None clears it."""
        from sqlalchemy import select

        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.repositories.stock_instance import StockInstanceRepository

        kind = db_session.scalars(select(ItemKind).where(ItemKind.code == "consumable")).first()
        assert kind is not None
        defn = ItemDefinition(
            name="Repo-BB-Clear", unit="pcs", kind_id=kind.id, stock_tracking_mode="none"
        )
        db_session.add(defn)
        db_session.flush()

        repo = StockInstanceRepository(db_session)
        inst = repo.create(
            definition_id=defn.id, best_before_date=date.today() + timedelta(days=10)
        )
        db_session.commit()

        repo.update(inst, set_best_before_date=True, best_before_date=None)
        db_session.commit()
        db_session.expire(inst)

        assert inst.best_before_date is None

    def test_repo_update_without_flag_preserves_best_before_date(self, db_session: Session) -> None:
        """Repository.update without set_best_before_date=True leaves the date alone."""
        from sqlalchemy import select

        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.repositories.stock_instance import StockInstanceRepository

        kind = db_session.scalars(select(ItemKind).where(ItemKind.code == "consumable")).first()
        assert kind is not None
        defn = ItemDefinition(
            name="Repo-BB-Preserve", unit="pcs", kind_id=kind.id, stock_tracking_mode="none"
        )
        db_session.add(defn)
        db_session.flush()

        target_date = date.today() + timedelta(days=15)
        repo = StockInstanceRepository(db_session)
        inst = repo.create(definition_id=defn.id, best_before_date=target_date)
        db_session.commit()

        # Update something else (e.g. serial) without touching best_before_date.
        repo.update(inst, set_serial=True, serial="SN-PRESERVED")
        db_session.commit()
        db_session.expire(inst)

        assert inst.best_before_date == target_date


# ---------------------------------------------------------------------------
# 7. HTTP API — end-to-end best_before_date tests
# ---------------------------------------------------------------------------


class TestHTTPBestBeforeDate:
    """HTTP-level tests for best_before_date on POST/GET/PATCH /instances."""

    def test_create_without_date_and_no_default_gives_null(self, test_client: TestClient) -> None:
        """POST /instances without best_before_date and no default → null in response."""
        defn = _create_definition(test_client, "No Default Item", stock_tracking_mode="exact")
        inst = _create_instance(test_client, defn["id"])
        assert inst["best_before_date"] is None

    def test_create_without_date_with_default_auto_computes(self, test_client: TestClient) -> None:
        """POST /instances without best_before_date + definition default N ⇒ today + N."""
        defn = _create_definition(
            test_client, "Milk", stock_tracking_mode="exact", default_best_before_days=7
        )
        inst = _create_instance(test_client, defn["id"])
        expected = (date.today() + timedelta(days=7)).isoformat()
        assert inst["best_before_date"] == expected

    def test_create_with_explicit_date_wins_over_default(self, test_client: TestClient) -> None:
        """POST /instances with explicit best_before_date wins over definition default."""
        defn = _create_definition(
            test_client, "Yogurt", stock_tracking_mode="exact", default_best_before_days=30
        )
        explicit = (date.today() + timedelta(days=3)).isoformat()
        inst = _create_instance(test_client, defn["id"], best_before_date=explicit)
        assert inst["best_before_date"] == explicit

    def test_create_with_explicit_past_date_stored_verbatim(self, test_client: TestClient) -> None:
        """POST /instances with an explicit past date stores it verbatim."""
        defn = _create_definition(
            test_client, "Old Stock", stock_tracking_mode="exact", default_best_before_days=30
        )
        past = (date.today() - timedelta(days=5)).isoformat()
        inst = _create_instance(test_client, defn["id"], best_before_date=past)
        assert inst["best_before_date"] == past

    def test_create_with_explicit_none_stays_null_even_with_default(
        self, test_client: TestClient
    ) -> None:
        """POST /instances with explicit null best_before_date stays null even with default."""
        defn = _create_definition(
            test_client, "Explicit Null", stock_tracking_mode="none", default_best_before_days=7
        )
        inst = _create_instance(test_client, defn["id"], best_before_date=None)
        assert inst["best_before_date"] is None

    def test_create_level_mode_auto_compute(self, test_client: TestClient) -> None:
        """POST /instances level mode: omitted + default ⇒ today + N."""
        defn = _create_definition(
            test_client,
            "Level Perishable",
            stock_tracking_mode="level",
            default_best_before_days=14,
        )
        inst = _create_instance(test_client, defn["id"], stock_level="high")
        expected = (date.today() + timedelta(days=14)).isoformat()
        assert inst["best_before_date"] == expected

    def test_create_none_mode_auto_compute(self, test_client: TestClient) -> None:
        """POST /instances none mode: omitted + default ⇒ today + N."""
        defn = _create_definition(
            test_client,
            "None Mode Perishable",
            stock_tracking_mode="none",
            default_best_before_days=21,
        )
        inst = _create_instance(test_client, defn["id"])
        expected = (date.today() + timedelta(days=21)).isoformat()
        assert inst["best_before_date"] == expected

    def test_response_includes_best_before_date_field_always(self, test_client: TestClient) -> None:
        """GET /instances/{id} always includes best_before_date (even when null)."""
        defn = _create_definition(test_client, "Always Present BB", stock_tracking_mode="exact")
        inst = _create_instance(test_client, defn["id"])
        assert "best_before_date" in inst

        get_resp = test_client.get(f"/api/instances/{inst['id']}")
        assert get_resp.status_code == 200
        assert "best_before_date" in get_resp.json()

    def test_get_returns_stored_best_before_date(self, test_client: TestClient) -> None:
        """GET /instances/{id} returns the stored best_before_date."""
        defn = _create_definition(
            test_client, "GET Test", stock_tracking_mode="exact", default_best_before_days=10
        )
        inst = _create_instance(test_client, defn["id"])
        expected = (date.today() + timedelta(days=10)).isoformat()

        get_resp = test_client.get(f"/api/instances/{inst['id']}")
        assert get_resp.status_code == 200
        assert get_resp.json()["best_before_date"] == expected

    def test_patch_sets_best_before_date(self, test_client: TestClient) -> None:
        """PATCH /instances/{id} can set best_before_date."""
        defn = _create_definition(test_client, "Patchable BB", stock_tracking_mode="exact")
        inst = _create_instance(test_client, defn["id"])
        assert inst["best_before_date"] is None

        new_date = (date.today() + timedelta(days=7)).isoformat()
        patch_resp = test_client.patch(
            f"/api/instances/{inst['id']}",
            json={"best_before_date": new_date},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["best_before_date"] == new_date

    def test_patch_clears_best_before_date_to_null(self, test_client: TestClient) -> None:
        """PATCH /instances/{id} with null best_before_date clears it."""
        defn = _create_definition(
            test_client, "Clearable BB", stock_tracking_mode="exact", default_best_before_days=7
        )
        inst = _create_instance(test_client, defn["id"])
        assert inst["best_before_date"] is not None

        patch_resp = test_client.patch(
            f"/api/instances/{inst['id']}",
            json={"best_before_date": None},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["best_before_date"] is None

    def test_patch_omitting_best_before_date_preserves_it(self, test_client: TestClient) -> None:
        """PATCH /instances/{id} without best_before_date leaves it unchanged."""
        defn = _create_definition(
            test_client, "Preserved BB", stock_tracking_mode="exact", default_best_before_days=7
        )
        inst = _create_instance(test_client, defn["id"])
        expected = inst["best_before_date"]
        assert expected is not None

        # PATCH a different field.
        patch_resp = test_client.patch(
            f"/api/instances/{inst['id']}",
            json={"serial": "SN-PRESERVED"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["best_before_date"] == expected

    def test_list_instances_includes_best_before_date(self, test_client: TestClient) -> None:
        """GET /instances includes best_before_date in each item."""
        defn = _create_definition(
            test_client, "List BB Test", stock_tracking_mode="exact", default_best_before_days=5
        )
        _create_instance(test_client, defn["id"])

        list_resp = test_client.get("/api/instances")
        assert list_resp.status_code == 200
        results = list_resp.json()
        item = next(i for i in results if i["definition_id"] == defn["id"])
        assert "best_before_date" in item
        assert item["best_before_date"] == (date.today() + timedelta(days=5)).isoformat()

    def test_non_retroactive_via_http(self, test_client: TestClient) -> None:
        """Changing definition's default_best_before_days does NOT alter existing lots."""
        defn = _create_definition(
            test_client, "Non-Retro BB", stock_tracking_mode="exact", default_best_before_days=7
        )
        inst = _create_instance(test_client, defn["id"])
        original_date = inst["best_before_date"]
        assert original_date is not None

        # Change the definition's default.
        patch_def_resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"default_best_before_days": 60},
        )
        assert patch_def_resp.status_code == 200

        # Existing lot must not change.
        get_resp = test_client.get(f"/api/instances/{inst['id']}")
        assert get_resp.status_code == 200
        assert get_resp.json()["best_before_date"] == original_date


# ---------------------------------------------------------------------------
# 8. _resolve_best_before unit tests (static helper)
# ---------------------------------------------------------------------------


class TestResolveBestBeforeHelper:
    """Direct unit tests for the _resolve_best_before static helper."""

    def test_omitted_no_default_returns_none(self) -> None:
        """_resolve_best_before: omitted + no default → None."""
        from unittest.mock import MagicMock

        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        data = InstanceCreate(definition_id=1)
        assert "best_before_date" not in data.model_fields_set

        defn = MagicMock()
        defn.default_best_before_days = None

        result = StockInstanceService._resolve_best_before(data, defn)
        assert result is None

    def test_omitted_with_default_returns_today_plus_n(self) -> None:
        """_resolve_best_before: omitted + default N → today + N."""
        from unittest.mock import MagicMock

        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        data = InstanceCreate(definition_id=1)
        defn = MagicMock()
        defn.default_best_before_days = 10

        result = StockInstanceService._resolve_best_before(data, defn)
        assert result == date.today() + timedelta(days=10)

    def test_explicit_date_wins_over_default(self) -> None:
        """_resolve_best_before: explicit date always wins."""
        from unittest.mock import MagicMock

        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        explicit_date = date.today() + timedelta(days=2)
        data = InstanceCreate(definition_id=1, best_before_date=explicit_date)
        assert "best_before_date" in data.model_fields_set

        defn = MagicMock()
        defn.default_best_before_days = 30  # would give today+30, but explicit wins

        result = StockInstanceService._resolve_best_before(data, defn)
        assert result == explicit_date

    def test_explicit_none_wins_over_default(self) -> None:
        """_resolve_best_before: explicit None wins over default."""
        from unittest.mock import MagicMock

        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        data = InstanceCreate(definition_id=1, best_before_date=None)
        assert "best_before_date" in data.model_fields_set

        defn = MagicMock()
        defn.default_best_before_days = 7  # would give today+7, but explicit None wins

        result = StockInstanceService._resolve_best_before(data, defn)
        assert result is None

    def test_explicit_past_date_wins_over_default(self) -> None:
        """_resolve_best_before: explicit past date wins (stored verbatim)."""
        from unittest.mock import MagicMock

        from app.schemas.stock_instance import InstanceCreate
        from app.services.stock_instance import StockInstanceService

        past_date = date.today() - timedelta(days=5)
        data = InstanceCreate(definition_id=1, best_before_date=past_date)

        defn = MagicMock()
        defn.default_best_before_days = 7

        result = StockInstanceService._resolve_best_before(data, defn)
        assert result == past_date


# ---------------------------------------------------------------------------
# 9. Alembic migration 0014
# ---------------------------------------------------------------------------


class TestAlembicMigration0014:
    """Migration 0014 must upgrade from 0013 and downgrade cleanly."""

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

    def test_upgrade_0014_adds_column(self) -> None:
        """Upgrading to 0014 adds best_before_date to stock_instances."""
        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "0014", url=url)
            assert rc == 0, f"alembic upgrade 0014 failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols_result = conn.execute(text("PRAGMA table_info(stock_instances)")).fetchall()
                col_names = {row[1] for row in cols_result}
                assert "best_before_date" in col_names, (
                    f"best_before_date missing; columns: {col_names}"
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0014_column_is_nullable(self) -> None:
        """After upgrade 0014, inserting a row without the column leaves it NULL."""
        url, db_path = _make_temp_db_url()
        try:
            # Upgrade to 0013 first.
            rc13, out13 = self._run_alembic("upgrade", "0013", url=url)
            assert rc13 == 0, f"upgrade 0013 failed:\n{out13}"

            engine = create_engine(url)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO item_definitions "
                        "(name, kind_id, unit, stock_tracking_mode) "
                        "VALUES ('Pre-existing', 1, 'pcs', 'exact')"
                    )
                )
                conn.execute(text("INSERT INTO stock_instances (definition_id) VALUES (1)"))

            # Upgrade to 0014.
            rc14, out14 = self._run_alembic("upgrade", "0014", url=url)
            assert rc14 == 0, f"upgrade 0014 failed:\n{out14}"

            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT best_before_date FROM stock_instances WHERE definition_id = 1")
                ).fetchone()
                assert row is not None
                assert row[0] is None, f"Expected NULL best_before_date, got {row[0]!r}"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_0014_drops_column(self) -> None:
        """Downgrading from 0014 to 0013 drops best_before_date."""
        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "0014", url=url)
            assert rc_up == 0, f"upgrade 0014 failed:\n{out_up}"

            rc_down, out_down = self._run_alembic("downgrade", "0013", url=url)
            assert rc_down == 0, f"downgrade to 0013 failed:\n{out_down}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols_result = conn.execute(text("PRAGMA table_info(stock_instances)")).fetchall()
                col_names = {row[1] for row in cols_result}
                assert "best_before_date" not in col_names, (
                    "best_before_date still present after downgrade to 0013"
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_head_includes_0014(self) -> None:
        """Upgrading to head includes the 0014 column."""
        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "head", url=url)
            assert rc == 0, f"upgrade head failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols_result = conn.execute(text("PRAGMA table_info(stock_instances)")).fetchall()
                col_names = {row[1] for row in cols_result}
                assert "best_before_date" in col_names, (
                    f"best_before_date missing after upgrade head; columns: {col_names}"
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_stepwise_upgrade_0013_to_0014(self) -> None:
        """Stepwise upgrade from 0013 to 0014 is clean."""
        url, db_path = _make_temp_db_url()
        try:
            rc13, out13 = self._run_alembic("upgrade", "0013", url=url)
            assert rc13 == 0, f"upgrade 0013 failed:\n{out13}"

            rc14, out14 = self._run_alembic("upgrade", "0014", url=url)
            assert rc14 == 0, f"upgrade 0014 failed:\n{out14}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols_result = conn.execute(text("PRAGMA table_info(stock_instances)")).fetchall()
                col_names = {row[1] for row in cols_result}
                assert "best_before_date" in col_names
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_full_roundtrip_upgrade_head_downgrade_base(self) -> None:
        """Full upgrade to head then downgrade to base is clean after adding 0014."""
        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "head", url=url)
            assert rc_up == 0, f"upgrade head failed:\n{out_up}"

            rc_down, out_down = self._run_alembic("downgrade", "base", url=url)
            assert rc_down == 0, f"downgrade base failed:\n{out_down}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'alembic_%' AND name != 'sqlite_sequence'"
                    )
                ).fetchall()
                assert tables == [], f"Tables still exist after downgrade: {tables}"
        finally:
            if db_path.exists():
                db_path.unlink()
