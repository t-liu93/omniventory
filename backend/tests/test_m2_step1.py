"""M2 Step 1 tests: per-definition stock tracking mode and min_stock.

Required coverage (per M2.md §9 Step 1 / §10 blind-review points):
- Default ``stock_tracking_mode`` is ``exact`` when omitted on create.
- Invalid ``stock_tracking_mode`` rejected with 422 + ``validation.unsupported_tracking_mode``.
- ``min_stock`` stored and echoed back on create/update/response.
- Migration ``0010`` upgrades and downgrades cleanly.
- Error code ``UNSUPPORTED_TRACKING_MODE`` is registered in ``ErrorCode``.
- All three valid modes (exact, level, none) accepted.
- ``min_stock`` can be set to None (cleared) via update.
- ``stock_tracking_mode`` can be changed to a valid mode via update.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_temp_db_url() -> tuple[str, Path]:
    """Return a (url, path) pair for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m2step1_")
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
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-m2-step1")
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
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.session as sess_mod
    import app.models.stock_instance as stock_instance_mod
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

            # Seed the three system kinds so definition endpoints can resolve defaults.
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

    from sqlalchemy import event

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.session as sess_mod
    import app.models.stock_instance as stock_instance_mod
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
    importlib.reload(loc_mod)

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


# ---------------------------------------------------------------------------
# Helper to create a definition via HTTP
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


# ---------------------------------------------------------------------------
# 1. Error code registration
# ---------------------------------------------------------------------------


class TestErrorCodeRegistration:
    """UNSUPPORTED_TRACKING_MODE must be registered in ErrorCode."""

    def test_error_code_constant_exists(self) -> None:
        """ErrorCode.UNSUPPORTED_TRACKING_MODE is defined."""
        from app.core.errors import ErrorCode

        assert hasattr(ErrorCode, "UNSUPPORTED_TRACKING_MODE")
        assert ErrorCode.UNSUPPORTED_TRACKING_MODE == "validation.unsupported_tracking_mode"


# ---------------------------------------------------------------------------
# 2. STOCK_TRACKING_MODES constant
# ---------------------------------------------------------------------------


class TestStockConstants:
    """app.core.stock constants are correct."""

    def test_tracking_modes_tuple(self) -> None:
        """STOCK_TRACKING_MODES contains exactly exact, level, none."""
        from app.core.stock import STOCK_TRACKING_MODES

        assert set(STOCK_TRACKING_MODES) == {"exact", "level", "none"}

    def test_stock_levels_tuple(self) -> None:
        """STOCK_LEVELS contains exactly high, medium, low."""
        from app.core.stock import STOCK_LEVELS

        assert set(STOCK_LEVELS) == {"high", "medium", "low"}


# ---------------------------------------------------------------------------
# 3. Default stock_tracking_mode = exact
# ---------------------------------------------------------------------------


class TestDefaultTrackingMode:
    """stock_tracking_mode defaults to 'exact' when omitted on create."""

    def test_default_mode_is_exact_via_http(self, test_client: TestClient) -> None:
        """POST /definitions without stock_tracking_mode → mode = 'exact'."""
        data = _create_definition(test_client, "No Mode Supplied")
        assert data["stock_tracking_mode"] == "exact"

    def test_default_mode_is_exact_response_field_present(self, test_client: TestClient) -> None:
        """Response always includes stock_tracking_mode even when not supplied."""
        data = _create_definition(test_client, "Implicit Exact")
        assert "stock_tracking_mode" in data

    def test_explicit_exact_mode_accepted(self, test_client: TestClient) -> None:
        """Explicitly providing mode='exact' returns 201."""
        data = _create_definition(test_client, "Explicit Exact", stock_tracking_mode="exact")
        assert data["stock_tracking_mode"] == "exact"

    def test_default_min_stock_is_null(self, test_client: TestClient) -> None:
        """min_stock defaults to None when not provided."""
        data = _create_definition(test_client, "No Min Stock")
        assert data["min_stock"] is None


# ---------------------------------------------------------------------------
# 4. Invalid mode rejected with 422
# ---------------------------------------------------------------------------


class TestInvalidTrackingMode:
    """Invalid stock_tracking_mode values rejected with 422."""

    def test_invalid_mode_returns_422(self, test_client: TestClient) -> None:
        """POST /definitions with stock_tracking_mode='bogus' returns 422."""
        resp = test_client.post(
            "/api/definitions",
            json={"name": "Bad Mode", "stock_tracking_mode": "bogus"},
        )
        assert resp.status_code == 422

    def test_invalid_mode_returns_correct_error_code(self, test_client: TestClient) -> None:
        """Error code is validation.unsupported_tracking_mode on invalid mode."""
        resp = test_client.post(
            "/api/definitions",
            json={"name": "Bad Mode", "stock_tracking_mode": "bogus"},
        )
        body = resp.json()
        assert body["code"] == "validation.unsupported_tracking_mode"

    def test_invalid_mode_returns_params(self, test_client: TestClient) -> None:
        """Error params include value and supported keys."""
        resp = test_client.post(
            "/api/definitions",
            json={"name": "Bad Mode", "stock_tracking_mode": "invalid"},
        )
        body = resp.json()
        params = body.get("params", {})
        assert params.get("value") == "invalid"
        assert "supported" in params
        assert set(params["supported"]) == {"exact", "level", "none"}

    def test_empty_string_mode_rejected(self, test_client: TestClient) -> None:
        """Empty string is rejected with 422."""
        resp = test_client.post(
            "/api/definitions",
            json={"name": "Empty Mode", "stock_tracking_mode": ""},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.unsupported_tracking_mode"

    def test_update_invalid_mode_rejected(self, test_client: TestClient) -> None:
        """PATCH /definitions/{id} with bad mode also returns 422."""
        defn = _create_definition(test_client, "Original")
        resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"stock_tracking_mode": "quarterly"},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.unsupported_tracking_mode"


# ---------------------------------------------------------------------------
# 5. All three valid modes accepted
# ---------------------------------------------------------------------------


class TestAllValidModes:
    """All three valid mode values are accepted on create and returned."""

    @pytest.mark.parametrize("mode", ["exact", "level", "none"])
    def test_all_modes_accepted_on_create(self, test_client: TestClient, mode: str) -> None:
        """POST /definitions with mode in {exact,level,none} returns 201."""
        data = _create_definition(test_client, f"Mode {mode}", stock_tracking_mode=mode)
        assert data["stock_tracking_mode"] == mode

    @pytest.mark.parametrize("mode", ["exact", "level", "none"])
    def test_all_modes_accepted_on_update(self, test_client: TestClient, mode: str) -> None:
        """PATCH /definitions/{id} with any valid mode succeeds."""
        defn = _create_definition(test_client, "Changeable")
        resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"stock_tracking_mode": mode},
        )
        assert resp.status_code == 200
        assert resp.json()["stock_tracking_mode"] == mode


# ---------------------------------------------------------------------------
# 6. min_stock stored and echoed back
# ---------------------------------------------------------------------------


class TestMinStock:
    """min_stock is stored and echoed on create/update/GET."""

    def test_min_stock_stored_on_create(self, test_client: TestClient) -> None:
        """POST /definitions with min_stock stores and returns the value."""
        data = _create_definition(test_client, "With Min Stock", min_stock="10.5")
        # API returns Decimal as string
        assert Decimal(data["min_stock"]) == Decimal("10.5")

    def test_min_stock_integer_stored(self, test_client: TestClient) -> None:
        """min_stock can be a whole number."""
        data = _create_definition(test_client, "Whole Min", min_stock="5")
        assert Decimal(data["min_stock"]) == Decimal("5")

    def test_min_stock_high_precision(self, test_client: TestClient) -> None:
        """min_stock can hold 6 decimal places (Numeric(18,6))."""
        data = _create_definition(test_client, "Precise Min", min_stock="3.141592")
        assert Decimal(data["min_stock"]) == Decimal("3.141592")

    def test_min_stock_returned_on_get(self, test_client: TestClient) -> None:
        """GET /definitions/{id} includes the stored min_stock."""
        defn = _create_definition(test_client, "Get Min", min_stock="7")
        get_resp = test_client.get(f"/api/definitions/{defn['id']}")
        assert get_resp.status_code == 200
        assert Decimal(get_resp.json()["min_stock"]) == Decimal("7")

    def test_min_stock_returned_in_list(self, test_client: TestClient) -> None:
        """GET /definitions includes min_stock in each definition."""
        _create_definition(test_client, "Listed Item", min_stock="4.25")
        list_resp = test_client.get("/api/definitions")
        assert list_resp.status_code == 200
        results = list_resp.json()
        item = next(d for d in results if d["name"] == "Listed Item")
        assert Decimal(item["min_stock"]) == Decimal("4.25")

    def test_min_stock_updated_via_patch(self, test_client: TestClient) -> None:
        """PATCH /definitions/{id} can set / change min_stock."""
        defn = _create_definition(test_client, "Patchable", min_stock="3")
        resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"min_stock": "8.75"},
        )
        assert resp.status_code == 200
        assert Decimal(resp.json()["min_stock"]) == Decimal("8.75")

    def test_min_stock_cleared_via_patch(self, test_client: TestClient) -> None:
        """PATCH /definitions/{id} with min_stock=null clears the threshold."""
        defn = _create_definition(test_client, "Clearable", min_stock="5")
        resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"min_stock": None},
        )
        assert resp.status_code == 200
        assert resp.json()["min_stock"] is None

    def test_min_stock_null_when_not_in_patch(self, test_client: TestClient) -> None:
        """PATCH without min_stock in the payload does NOT clear the existing value."""
        defn = _create_definition(test_client, "Preserved", min_stock="2.5")
        resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"name": "Preserved Renamed"},
        )
        assert resp.status_code == 200
        # min_stock should be unchanged (still 2.5)
        assert Decimal(resp.json()["min_stock"]) == Decimal("2.5")


# ---------------------------------------------------------------------------
# 7. Service layer unit tests
# ---------------------------------------------------------------------------


class TestItemDefinitionServiceM2:
    """ItemDefinitionService unit tests for M2 Step 1 logic.

    These use the ``db_session`` fixture which already seeds item_kinds
    (durable/consumable/perishable), so no separate kind setup is needed.
    """

    def test_create_default_tracking_mode(self, db_session: Session) -> None:
        """Service.create with default mode sets stock_tracking_mode='exact'."""
        from app.schemas.item_definition import DefinitionCreate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        defn = svc.create(DefinitionCreate(name="Service Test"))
        db_session.commit()
        assert defn.stock_tracking_mode == "exact"

    def test_create_invalid_mode_raises_app_error(self, db_session: Session) -> None:
        """Service.create with bad mode raises AppError 422 with correct code."""
        from app.core.errors import AppError, ErrorCode
        from app.schemas.item_definition import DefinitionCreate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.create(DefinitionCreate(name="Bad", stock_tracking_mode="nope"))
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_TRACKING_MODE

    def test_create_invalid_mode_params(self, db_session: Session) -> None:
        """AppError params contain value and supported."""
        from app.core.errors import AppError
        from app.schemas.item_definition import DefinitionCreate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.create(DefinitionCreate(name="Bad", stock_tracking_mode="weekly"))
        params = exc_info.value.params or {}
        assert params.get("value") == "weekly"
        assert "supported" in params

    def test_create_with_min_stock(self, db_session: Session) -> None:
        """Service.create stores min_stock correctly."""
        from app.schemas.item_definition import DefinitionCreate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        defn = svc.create(DefinitionCreate(name="With Min", min_stock=Decimal("12.5")))
        db_session.commit()
        assert defn.min_stock == Decimal("12.5")

    def test_update_mode_validated(self, db_session: Session) -> None:
        """Service.update validates mode if provided."""
        from app.core.errors import AppError, ErrorCode
        from app.schemas.item_definition import DefinitionCreate, DefinitionUpdate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        defn = svc.create(DefinitionCreate(name="Orig"))
        db_session.commit()

        with pytest.raises(AppError) as exc_info:
            svc.update(defn.id, DefinitionUpdate(stock_tracking_mode="bad"))
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_TRACKING_MODE

    def test_update_mode_not_changed_when_absent(self, db_session: Session) -> None:
        """Service.update without stock_tracking_mode leaves the mode intact."""
        from app.schemas.item_definition import DefinitionCreate, DefinitionUpdate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        defn = svc.create(DefinitionCreate(name="Level Item", stock_tracking_mode="level"))
        db_session.commit()

        updated = svc.update(defn.id, DefinitionUpdate(name="Level Item Renamed"))
        assert updated.stock_tracking_mode == "level"  # unchanged


# ---------------------------------------------------------------------------
# 8. Alembic migration 0010
# ---------------------------------------------------------------------------


class TestAlembicMigration0010:
    """Migration 0010 must upgrade and downgrade cleanly."""

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

    def test_upgrade_0010_adds_columns(self) -> None:
        """Upgrading to 0010 adds stock_tracking_mode and min_stock to item_definitions."""
        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "0010", url=url)
            assert rc == 0, f"alembic upgrade 0010 failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols_result = conn.execute(text("PRAGMA table_info(item_definitions)")).fetchall()
                col_names = {row[1] for row in cols_result}
                assert "stock_tracking_mode" in col_names, (
                    f"stock_tracking_mode missing; columns: {col_names}"
                )
                assert "min_stock" in col_names, f"min_stock missing; columns: {col_names}"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0010_server_default_is_exact(self) -> None:
        """After upgrade 0010, inserting a row without stock_tracking_mode defaults to 'exact'."""
        url, db_path = _make_temp_db_url()
        try:
            # Upgrade to just before 0010 to insert a definition, then apply 0010.
            rc0, out0 = self._run_alembic("upgrade", "0009", url=url)
            assert rc0 == 0, f"upgrade 0009 failed:\n{out0}"

            # Insert a definition with minimum required columns (before 0010 columns exist).
            engine = create_engine(url)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO item_definitions (name, kind_id, unit) "
                        "VALUES ('Pre-existing', 1, 'pcs')"
                    )
                )

            rc10, out10 = self._run_alembic("upgrade", "0010", url=url)
            assert rc10 == 0, f"upgrade 0010 failed:\n{out10}"

            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT stock_tracking_mode, min_stock FROM item_definitions LIMIT 1")
                ).fetchone()
                assert row is not None
                assert row[0] == "exact", f"Expected 'exact', got {row[0]!r}"
                assert row[1] is None, f"Expected NULL min_stock, got {row[1]!r}"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_0010_drops_columns(self) -> None:
        """Downgrading from 0010 to 0009 drops stock_tracking_mode and min_stock."""
        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "0010", url=url)
            assert rc_up == 0, f"upgrade 0010 failed:\n{out_up}"

            rc_down, out_down = self._run_alembic("downgrade", "0009", url=url)
            assert rc_down == 0, f"downgrade to 0009 failed:\n{out_down}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols_result = conn.execute(text("PRAGMA table_info(item_definitions)")).fetchall()
                col_names = {row[1] for row in cols_result}
                assert "stock_tracking_mode" not in col_names, (
                    "stock_tracking_mode still present after downgrade"
                )
                assert "min_stock" not in col_names, "min_stock still present after downgrade"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_head_and_downgrade_base_roundtrip(self) -> None:
        """Full upgrade to head then downgrade to base is clean."""
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

    def test_stepwise_upgrade_0009_to_0010(self) -> None:
        """Stepwise upgrade from 0009 to 0010 is clean."""
        url, db_path = _make_temp_db_url()
        try:
            rc09, out09 = self._run_alembic("upgrade", "0009", url=url)
            assert rc09 == 0, f"upgrade 0009 failed:\n{out09}"

            rc10, out10 = self._run_alembic("upgrade", "0010", url=url)
            assert rc10 == 0, f"upgrade 0010 failed:\n{out10}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "item_definitions" in table_names
        finally:
            if db_path.exists():
                db_path.unlink()
