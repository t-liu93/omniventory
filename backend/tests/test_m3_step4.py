"""M3 Step 4 tests: computed expiring/expired endpoint (GET /expiring).

Required coverage (per M3.md §5 "Backend" expiring-read + §9 Step 4 + §10
blind-review checkpoints):

Repository (StockInstanceRepository.list_expiring):
- best_before < today         → included (expired lot)
- today <= best_before <= cutoff → included (expiring lot)
- boundary: best_before == cutoff (today+N) → INCLUDED
- boundary: best_before == cutoff+1 (today+N+1) → EXCLUDED
- depleted exact lot (quantity == 0) → EXCLUDED
- level/none lot (quantity IS NULL) with a date → INCLUDED
- ordering: soonest-first (expired lots naturally lead)
- no qualifying lots → empty list

Service (ExpiryService.compute):
- status='expired', days_remaining<0 for past dates
- status='expiring', days_remaining>=0 for today/future dates
- within_days=0 → only expired + expiring-today
- negative within_days clamped to 0 (not rejected)
- days_remaining is (best_before_date - today).days (can be negative)
- name resolved from definition (no N+1)

HTTP API (end-to-end via TestClient):
- GET /expiring returns 200 + correct JSON list
- GET /expiring returns 401 when unauthenticated
- GET /expiring returns [] when nothing qualifies
- within_days param accepted (default 30)
- response shape matches ExpiringItem schema
"""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Generator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Fixtures & helpers
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
    name: str = "Test Item",
    mode: str = "exact",
) -> object:
    """Seed a minimal ItemDefinition."""
    from app.models.item_definition import ItemDefinition
    from app.models.item_kind import ItemKind

    kind = session.scalars(select(ItemKind).where(ItemKind.code == "perishable")).first()
    assert kind is not None
    defn = ItemDefinition(
        name=name,
        unit="pcs",
        kind_id=kind.id,
        stock_tracking_mode=mode,
    )
    session.add(defn)
    session.flush()
    return defn


def _seed_lot(
    session: Session,
    definition_id: int,
    *,
    best_before_date: date | None,
    quantity: Decimal | None = Decimal("5"),
    stock_level: str | None = None,
) -> object:
    """Seed a StockInstance with the given best_before_date and quantity."""
    from app.models.stock_instance import StockInstance

    inst = StockInstance(
        definition_id=definition_id,
        best_before_date=best_before_date,
        quantity=quantity,
        stock_level=stock_level,
    )
    session.add(inst)
    session.flush()
    return inst


# ---------------------------------------------------------------------------
# 1. Repository: list_expiring filter and ordering
# ---------------------------------------------------------------------------


class TestListExpiringRepository:
    """StockInstanceRepository.list_expiring filter + ordering invariants."""

    def test_expired_lot_is_included(self, db_session: Session) -> None:
        """A lot with best_before < today is included (expired)."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session)
        yesterday = date.today() - timedelta(days=1)
        lot = _seed_lot(db_session, defn.id, best_before_date=yesterday)
        db_session.commit()

        repo = StockInstanceRepository(db_session)
        results = repo.list_expiring(date.today() + timedelta(days=30))

        assert any(r.id == lot.id for r in results)

    def test_expiring_lot_is_included(self, db_session: Session) -> None:
        """A lot with today <= best_before <= cutoff is included (expiring)."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session)
        future = date.today() + timedelta(days=7)
        lot = _seed_lot(db_session, defn.id, best_before_date=future)
        db_session.commit()

        repo = StockInstanceRepository(db_session)
        results = repo.list_expiring(date.today() + timedelta(days=30))

        assert any(r.id == lot.id for r in results)

    def test_boundary_cutoff_date_included(self, db_session: Session) -> None:
        """Lot with best_before == cutoff (today+N) is INCLUDED."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session)
        n = 30
        cutoff = date.today() + timedelta(days=n)
        lot = _seed_lot(db_session, defn.id, best_before_date=cutoff)
        db_session.commit()

        repo = StockInstanceRepository(db_session)
        results = repo.list_expiring(cutoff)

        assert any(r.id == lot.id for r in results)

    def test_boundary_beyond_cutoff_excluded(self, db_session: Session) -> None:
        """Lot with best_before == cutoff+1 (today+N+1) is EXCLUDED."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session)
        n = 30
        beyond_cutoff = date.today() + timedelta(days=n + 1)
        _seed_lot(db_session, defn.id, best_before_date=beyond_cutoff)
        db_session.commit()

        repo = StockInstanceRepository(db_session)
        results = repo.list_expiring(date.today() + timedelta(days=n))

        assert results == []

    def test_depleted_exact_lot_excluded(self, db_session: Session) -> None:
        """An exact lot with quantity==0 is EXCLUDED (depleted batch)."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session, mode="exact")
        yesterday = date.today() - timedelta(days=1)
        _seed_lot(db_session, defn.id, best_before_date=yesterday, quantity=Decimal("0"))
        db_session.commit()

        repo = StockInstanceRepository(db_session)
        results = repo.list_expiring(date.today() + timedelta(days=30))

        assert results == []

    def test_level_lot_with_null_quantity_included(self, db_session: Session) -> None:
        """A level-mode lot (quantity IS NULL) with a date is INCLUDED (presence-based)."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session, mode="level")
        yesterday = date.today() - timedelta(days=1)
        lot = _seed_lot(
            db_session,
            defn.id,
            best_before_date=yesterday,
            quantity=None,
            stock_level="low",
        )
        db_session.commit()

        repo = StockInstanceRepository(db_session)
        results = repo.list_expiring(date.today() + timedelta(days=30))

        assert any(r.id == lot.id for r in results)

    def test_none_mode_lot_with_null_quantity_included(self, db_session: Session) -> None:
        """A none-mode lot (quantity IS NULL) with a date is INCLUDED."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session, mode="none")
        yesterday = date.today() - timedelta(days=1)
        lot = _seed_lot(db_session, defn.id, best_before_date=yesterday, quantity=None)
        db_session.commit()

        repo = StockInstanceRepository(db_session)
        results = repo.list_expiring(date.today() + timedelta(days=30))

        assert any(r.id == lot.id for r in results)

    def test_lot_with_no_date_excluded(self, db_session: Session) -> None:
        """A lot with best_before_date IS NULL is never included."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session)
        _seed_lot(db_session, defn.id, best_before_date=None)
        db_session.commit()

        repo = StockInstanceRepository(db_session)
        results = repo.list_expiring(date.today() + timedelta(days=30))

        assert results == []

    def test_ordering_soonest_first(self, db_session: Session) -> None:
        """Results are ordered soonest-first (expired lots lead)."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session)
        far_expiring = date.today() + timedelta(days=20)
        near_expiring = date.today() + timedelta(days=5)
        already_expired = date.today() - timedelta(days=3)

        lot_far = _seed_lot(db_session, defn.id, best_before_date=far_expiring)
        lot_near = _seed_lot(db_session, defn.id, best_before_date=near_expiring)
        lot_expired = _seed_lot(db_session, defn.id, best_before_date=already_expired)
        db_session.commit()

        repo = StockInstanceRepository(db_session)
        results = repo.list_expiring(date.today() + timedelta(days=30))

        ids = [r.id for r in results]
        assert ids.index(lot_expired.id) < ids.index(lot_near.id)  # type: ignore[union-attr]
        assert ids.index(lot_near.id) < ids.index(lot_far.id)  # type: ignore[union-attr]

    def test_empty_when_nothing_qualifies(self, db_session: Session) -> None:
        """Returns [] when no lots have a qualifying best_before_date."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session)
        # Only far-future lot — excluded by cutoff
        _seed_lot(db_session, defn.id, best_before_date=date.today() + timedelta(days=60))
        db_session.commit()

        repo = StockInstanceRepository(db_session)
        results = repo.list_expiring(date.today() + timedelta(days=30))

        assert results == []

    def test_definition_name_accessible_on_result(self, db_session: Session) -> None:
        """definition.name is accessible on returned lots (eager-loaded, no N+1)."""
        from app.repositories.stock_instance import StockInstanceRepository

        defn = _seed_definition(db_session, name="Milk")
        yesterday = date.today() - timedelta(days=1)
        _seed_lot(db_session, defn.id, best_before_date=yesterday)
        db_session.commit()

        repo = StockInstanceRepository(db_session)
        results = repo.list_expiring(date.today() + timedelta(days=30))

        assert len(results) == 1
        assert results[0].definition.name == "Milk"


# ---------------------------------------------------------------------------
# 2. ExpiryService: status, days_remaining, clamp
# ---------------------------------------------------------------------------


class TestExpiryServiceStatusAndDaysRemaining:
    """ExpiryService.compute — status and days_remaining derivation."""

    def test_expired_lot_has_status_expired_and_negative_days(self, db_session: Session) -> None:
        """A lot with best_before < today gets status='expired', days_remaining < 0."""
        from app.services.expiry import ExpiryService

        defn = _seed_definition(db_session, name="Yogurt")
        yesterday = date.today() - timedelta(days=1)
        _seed_lot(db_session, defn.id, best_before_date=yesterday)
        db_session.commit()

        svc = ExpiryService(db_session)
        results = svc.compute(within_days=30)

        assert len(results) == 1
        item = results[0]
        assert item.status == "expired"
        assert item.days_remaining == -1
        assert item.days_remaining < 0

    def test_expiring_today_has_status_expiring_and_zero_days(self, db_session: Session) -> None:
        """A lot with best_before == today gets status='expiring', days_remaining == 0."""
        from app.services.expiry import ExpiryService

        defn = _seed_definition(db_session, name="Cheese")
        today = date.today()
        _seed_lot(db_session, defn.id, best_before_date=today)
        db_session.commit()

        svc = ExpiryService(db_session)
        results = svc.compute(within_days=30)

        assert len(results) == 1
        item = results[0]
        assert item.status == "expiring"
        assert item.days_remaining == 0

    def test_expiring_future_has_status_expiring_and_positive_days(
        self, db_session: Session
    ) -> None:
        """A lot with best_before > today gets status='expiring', days_remaining > 0."""
        from app.services.expiry import ExpiryService

        defn = _seed_definition(db_session, name="Butter")
        future = date.today() + timedelta(days=7)
        _seed_lot(db_session, defn.id, best_before_date=future)
        db_session.commit()

        svc = ExpiryService(db_session)
        results = svc.compute(within_days=30)

        assert len(results) == 1
        item = results[0]
        assert item.status == "expiring"
        assert item.days_remaining == 7
        assert item.days_remaining > 0

    def test_within_days_zero_includes_only_expired_and_today(self, db_session: Session) -> None:
        """within_days=0 → only expired and expiring-today lots."""
        from app.services.expiry import ExpiryService

        defn = _seed_definition(db_session, name="Multi")
        yesterday = date.today() - timedelta(days=1)
        today = date.today()
        tomorrow = date.today() + timedelta(days=1)

        lot_expired = _seed_lot(db_session, defn.id, best_before_date=yesterday)
        lot_today = _seed_lot(db_session, defn.id, best_before_date=today)
        _seed_lot(db_session, defn.id, best_before_date=tomorrow)
        db_session.commit()

        svc = ExpiryService(db_session)
        results = svc.compute(within_days=0)

        result_ids = {r.instance_id for r in results}
        assert lot_expired.id in result_ids  # type: ignore[union-attr]
        assert lot_today.id in result_ids  # type: ignore[union-attr]
        assert len(results) == 2

    def test_negative_within_days_clamped_to_zero(self, db_session: Session) -> None:
        """Negative within_days is clamped to 0 — behaves as within_days=0."""
        from app.services.expiry import ExpiryService

        defn = _seed_definition(db_session, name="Clamp")
        yesterday = date.today() - timedelta(days=1)
        today = date.today()
        tomorrow = date.today() + timedelta(days=1)

        lot_expired = _seed_lot(db_session, defn.id, best_before_date=yesterday)
        lot_today = _seed_lot(db_session, defn.id, best_before_date=today)
        _seed_lot(db_session, defn.id, best_before_date=tomorrow)
        db_session.commit()

        svc = ExpiryService(db_session)
        results_negative = svc.compute(within_days=-99)
        results_zero = svc.compute(within_days=0)

        # Negative is clamped to 0, not rejected — identical result
        assert {r.instance_id for r in results_negative} == {r.instance_id for r in results_zero}
        result_ids = {r.instance_id for r in results_negative}
        assert lot_expired.id in result_ids  # type: ignore[union-attr]
        assert lot_today.id in result_ids  # type: ignore[union-attr]

    def test_depleted_exact_lot_excluded_from_service(self, db_session: Session) -> None:
        """A depleted exact lot (quantity==0) is excluded from the service result."""
        from app.services.expiry import ExpiryService

        defn = _seed_definition(db_session, mode="exact")
        yesterday = date.today() - timedelta(days=1)
        _seed_lot(db_session, defn.id, best_before_date=yesterday, quantity=Decimal("0"))
        db_session.commit()

        svc = ExpiryService(db_session)
        results = svc.compute(within_days=30)

        assert results == []

    def test_level_lot_with_null_quantity_included_in_service(self, db_session: Session) -> None:
        """A level-mode lot (quantity IS NULL) is included (presence-based)."""
        from app.services.expiry import ExpiryService

        defn = _seed_definition(db_session, mode="level")
        yesterday = date.today() - timedelta(days=1)
        lot = _seed_lot(
            db_session,
            defn.id,
            best_before_date=yesterday,
            quantity=None,
            stock_level="medium",
        )
        db_session.commit()

        svc = ExpiryService(db_session)
        results = svc.compute(within_days=30)

        assert len(results) == 1
        item = results[0]
        assert item.instance_id == lot.id  # type: ignore[union-attr]
        assert item.quantity is None
        assert item.status == "expired"

    def test_name_resolved_from_definition(self, db_session: Session) -> None:
        """name field in ExpiringItem comes from the definition, not the lot."""
        from app.services.expiry import ExpiryService

        defn = _seed_definition(db_session, name="Fresh Milk")
        yesterday = date.today() - timedelta(days=2)
        _seed_lot(db_session, defn.id, best_before_date=yesterday)
        db_session.commit()

        svc = ExpiryService(db_session)
        results = svc.compute(within_days=30)

        assert len(results) == 1
        assert results[0].name == "Fresh Milk"
        assert results[0].definition_id == defn.id  # type: ignore[union-attr]

    def test_empty_result_when_nothing_qualifies(self, db_session: Session) -> None:
        """Returns [] when no lots have a qualifying best_before_date."""
        from app.services.expiry import ExpiryService

        defn = _seed_definition(db_session)
        # Far future — beyond the 30-day window
        _seed_lot(db_session, defn.id, best_before_date=date.today() + timedelta(days=60))
        db_session.commit()

        svc = ExpiryService(db_session)
        results = svc.compute(within_days=30)

        assert results == []

    def test_ordering_soonest_first_in_service(self, db_session: Session) -> None:
        """Service returns lots ordered soonest-first (expired naturally leads)."""
        from app.services.expiry import ExpiryService

        defn = _seed_definition(db_session)
        d_expired = date.today() - timedelta(days=5)
        d_near = date.today() + timedelta(days=3)
        d_far = date.today() + timedelta(days=15)

        lot_far = _seed_lot(db_session, defn.id, best_before_date=d_far)
        lot_near = _seed_lot(db_session, defn.id, best_before_date=d_near)
        lot_expired = _seed_lot(db_session, defn.id, best_before_date=d_expired)
        db_session.commit()

        svc = ExpiryService(db_session)
        results = svc.compute(within_days=30)

        ids = [r.instance_id for r in results]
        assert ids.index(lot_expired.id) < ids.index(lot_near.id)  # type: ignore[union-attr]
        assert ids.index(lot_near.id) < ids.index(lot_far.id)  # type: ignore[union-attr]

    def test_boundary_exact_cutoff_in_service(self, db_session: Session) -> None:
        """Lot with best_before == today+N is included; today+N+1 is excluded."""
        from app.services.expiry import ExpiryService

        defn = _seed_definition(db_session)
        n = 30
        on_cutoff = date.today() + timedelta(days=n)
        beyond_cutoff = date.today() + timedelta(days=n + 1)

        lot_on = _seed_lot(db_session, defn.id, best_before_date=on_cutoff)
        _seed_lot(db_session, defn.id, best_before_date=beyond_cutoff)
        db_session.commit()

        svc = ExpiryService(db_session)
        results = svc.compute(within_days=n)

        result_ids = {r.instance_id for r in results}
        assert lot_on.id in result_ids  # type: ignore[union-attr]
        assert len(results) == 1

    def test_location_id_propagated(self, db_session: Session) -> None:
        """location_id is propagated from the lot to the ExpiringItem."""
        from app.services.expiry import ExpiryService

        defn = _seed_definition(db_session)
        yesterday = date.today() - timedelta(days=1)
        # Lot with no location (NULL)
        _seed_lot(db_session, defn.id, best_before_date=yesterday)
        db_session.commit()

        svc = ExpiryService(db_session)
        results = svc.compute(within_days=30)

        assert len(results) == 1
        assert results[0].location_id is None


# ---------------------------------------------------------------------------
# 3. HTTP API (end-to-end via TestClient)
# ---------------------------------------------------------------------------


def _make_temp_db_url() -> tuple[str, Path]:
    """Return (url, path) for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m3step4_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()
    return f"sqlite:///{path_str}", path


@pytest.fixture()
def temp_db_step4(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite DB for HTTP-level tests."""
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m3-step4")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def http_client(temp_db_step4: Path) -> Generator[object]:  # noqa: ARG001
    """TestClient with full schema + authenticated admin session."""
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


@pytest.fixture()
def http_client_no_auth(temp_db_step4: Path) -> Generator[object]:  # noqa: ARG001
    """TestClient without authentication (for 401 tests)."""
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


class TestExpiringEndpoint:
    """GET /expiring HTTP API tests."""

    def test_unauthenticated_returns_401(self, http_client_no_auth: object) -> None:
        """GET /expiring without a session cookie returns 401."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client_no_auth, TestClient)
        resp = http_client_no_auth.get("/api/expiring")
        assert resp.status_code == 401

    def test_empty_when_nothing_qualifies(self, http_client: object) -> None:
        """GET /expiring returns [] when no lots qualify."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)
        resp = http_client.get("/api/expiring")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_expired_lot_with_correct_shape(self, http_client: object) -> None:
        """GET /expiring returns an expired lot with correct ExpiringItem shape."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)

        kind_resp = http_client.get("/api/kinds")
        assert kind_resp.status_code == 200
        kind_id = next(k["id"] for k in kind_resp.json() if k["code"] == "perishable")

        defn_resp = http_client.post(
            "/api/definitions",
            json={
                "name": "Organic Milk",
                "kind_id": kind_id,
                "unit": "litre",
                "stock_tracking_mode": "exact",
            },
        )
        assert defn_resp.status_code == 201
        defn_id = defn_resp.json()["id"]

        yesterday_str = (date.today() - timedelta(days=1)).isoformat()
        inst_resp = http_client.post(
            "/api/instances",
            json={
                "definition_id": defn_id,
                "quantity": "2",
                "best_before_date": yesterday_str,
            },
        )
        assert inst_resp.status_code == 201
        inst_id = inst_resp.json()["id"]

        resp = http_client.get("/api/expiring?within_days=30")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data) == 1
        item = data[0]
        assert item["instance_id"] == inst_id
        assert item["definition_id"] == defn_id
        assert item["name"] == "Organic Milk"
        assert item["best_before_date"] == yesterday_str
        assert item["status"] == "expired"
        assert item["days_remaining"] == -1
        assert Decimal(item["quantity"]) == Decimal("2")

    def test_returns_expiring_lot(self, http_client: object) -> None:
        """GET /expiring returns an expiring-soon lot with status='expiring'."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)

        kind_resp = http_client.get("/api/kinds")
        kind_id = next(k["id"] for k in kind_resp.json() if k["code"] == "perishable")

        defn_resp = http_client.post(
            "/api/definitions",
            json={
                "name": "Fresh Cheese",
                "kind_id": kind_id,
                "unit": "block",
                "stock_tracking_mode": "exact",
            },
        )
        defn_id = defn_resp.json()["id"]

        in_5_days = (date.today() + timedelta(days=5)).isoformat()
        inst_resp = http_client.post(
            "/api/instances",
            json={
                "definition_id": defn_id,
                "quantity": "1",
                "best_before_date": in_5_days,
            },
        )
        assert inst_resp.status_code == 201

        resp = http_client.get("/api/expiring?within_days=30")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data) == 1
        item = data[0]
        assert item["name"] == "Fresh Cheese"
        assert item["status"] == "expiring"
        assert item["days_remaining"] == 5

    def test_within_days_default_is_30(self, http_client: object) -> None:
        """GET /expiring (no param) defaults to within_days=30."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)

        kind_resp = http_client.get("/api/kinds")
        kind_id = next(k["id"] for k in kind_resp.json() if k["code"] == "perishable")

        defn_resp = http_client.post(
            "/api/definitions",
            json={
                "name": "Butter",
                "kind_id": kind_id,
                "unit": "pack",
                "stock_tracking_mode": "exact",
            },
        )
        defn_id = defn_resp.json()["id"]

        in_30_days = (date.today() + timedelta(days=30)).isoformat()
        in_31_days = (date.today() + timedelta(days=31)).isoformat()

        # Lot at exactly 30 days — should be included with default param
        http_client.post(
            "/api/instances",
            json={"definition_id": defn_id, "quantity": "1", "best_before_date": in_30_days},
        )
        # Lot at 31 days — excluded with default param
        http_client.post(
            "/api/instances",
            json={"definition_id": defn_id, "quantity": "1", "best_before_date": in_31_days},
        )

        resp = http_client.get("/api/expiring")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["best_before_date"] == in_30_days

    def test_level_lot_with_null_quantity_included_via_api(self, http_client: object) -> None:
        """A level-mode lot (quantity IS NULL) with a date appears in the API response."""
        from fastapi.testclient import TestClient

        assert isinstance(http_client, TestClient)

        kind_resp = http_client.get("/api/kinds")
        kind_id = next(k["id"] for k in kind_resp.json() if k["code"] == "perishable")

        defn_resp = http_client.post(
            "/api/definitions",
            json={
                "name": "Assorted Fruit",
                "kind_id": kind_id,
                "unit": "bowl",
                "stock_tracking_mode": "level",
            },
        )
        defn_id = defn_resp.json()["id"]

        yesterday_str = (date.today() - timedelta(days=1)).isoformat()
        inst_resp = http_client.post(
            "/api/instances",
            json={
                "definition_id": defn_id,
                "stock_level": "medium",
                "best_before_date": yesterday_str,
            },
        )
        assert inst_resp.status_code == 201

        resp = http_client.get("/api/expiring?within_days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["quantity"] is None
        assert data[0]["status"] == "expired"
        assert data[0]["name"] == "Assorted Fruit"
