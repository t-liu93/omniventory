"""M3 Step 1 tests: per-definition default_best_before_days.

Required coverage (per M3.md §5 "Backend" + §9 Step 1 "Tests" + §10 blind-review
checkpoints):

- ``default_best_before_days`` stored and echoed on create / update / GET.
- Default is ``None`` when omitted on create.
- ``default_best_before_days < 0`` ⇒ 422 ``validation.invalid_input``.
- ``0`` accepted (same-day expiry).
- Clearing to ``None`` via PATCH also works.
- Migration ``0013`` upgrades cleanly on a DB at ``0012`` and downgrades cleanly.
- Service-layer unit tests matching the pattern of ``test_m2_step1.py``.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m3step1_")
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
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-m3-step1")
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
    import app.models.audit_log as audit_log_mod
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
# 1. Default value (None when omitted)
# ---------------------------------------------------------------------------


class TestDefaultBestBeforeDaysDefault:
    """default_best_before_days defaults to None when omitted on create."""

    def test_default_is_none_on_create(self, test_client: TestClient) -> None:
        """POST /definitions without default_best_before_days → field is null."""
        data = _create_definition(test_client, "No Shelf Life")
        assert data["default_best_before_days"] is None

    def test_field_always_present_in_response(self, test_client: TestClient) -> None:
        """Response always includes default_best_before_days even when null."""
        data = _create_definition(test_client, "Always Present")
        assert "default_best_before_days" in data


# ---------------------------------------------------------------------------
# 2. Stored and echoed on create / GET / PATCH
# ---------------------------------------------------------------------------


class TestDefaultBestBeforeDaysStoredAndEchoed:
    """default_best_before_days is stored and echoed correctly."""

    def test_stored_on_create_positive(self, test_client: TestClient) -> None:
        """POST /definitions with default_best_before_days=7 stores and echoes 7."""
        data = _create_definition(test_client, "Milk", default_best_before_days=7)
        assert data["default_best_before_days"] == 7

    def test_stored_on_create_zero(self, test_client: TestClient) -> None:
        """POST /definitions with default_best_before_days=0 stores and echoes 0 (same-day)."""
        data = _create_definition(test_client, "Same Day", default_best_before_days=0)
        assert data["default_best_before_days"] == 0

    def test_stored_on_create_large_value(self, test_client: TestClient) -> None:
        """default_best_before_days can be a large number (e.g. 3650 = 10 years)."""
        data = _create_definition(test_client, "Wine", default_best_before_days=3650)
        assert data["default_best_before_days"] == 3650

    def test_returned_on_get(self, test_client: TestClient) -> None:
        """GET /definitions/{id} includes the stored default_best_before_days."""
        defn = _create_definition(test_client, "Cheese", default_best_before_days=30)
        get_resp = test_client.get(f"/api/definitions/{defn['id']}")
        assert get_resp.status_code == 200
        assert get_resp.json()["default_best_before_days"] == 30

    def test_returned_in_list(self, test_client: TestClient) -> None:
        """GET /definitions includes default_best_before_days in each definition."""
        _create_definition(test_client, "Listed Perishable", default_best_before_days=14)
        list_resp = test_client.get("/api/definitions")
        assert list_resp.status_code == 200
        results = list_resp.json()
        item = next(d for d in results if d["name"] == "Listed Perishable")
        assert item["default_best_before_days"] == 14

    def test_updated_via_patch(self, test_client: TestClient) -> None:
        """PATCH /definitions/{id} can set / change default_best_before_days."""
        defn = _create_definition(test_client, "Patchable", default_best_before_days=7)
        resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"default_best_before_days": 14},
        )
        assert resp.status_code == 200
        assert resp.json()["default_best_before_days"] == 14

    def test_cleared_via_patch_to_null(self, test_client: TestClient) -> None:
        """PATCH /definitions/{id} with default_best_before_days=null clears the default."""
        defn = _create_definition(test_client, "Clearable", default_best_before_days=7)
        resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"default_best_before_days": None},
        )
        assert resp.status_code == 200
        assert resp.json()["default_best_before_days"] is None

    def test_not_changed_when_absent_from_patch(self, test_client: TestClient) -> None:
        """PATCH without default_best_before_days in the payload does NOT clear it."""
        defn = _create_definition(test_client, "Preserved Shelf", default_best_before_days=21)
        resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"name": "Preserved Shelf Renamed"},
        )
        assert resp.status_code == 200
        # default_best_before_days should be unchanged
        assert resp.json()["default_best_before_days"] == 21


# ---------------------------------------------------------------------------
# 3. Negative value rejected with 422 validation.invalid_input
# ---------------------------------------------------------------------------


class TestDefaultBestBeforeDaysValidation:
    """default_best_before_days < 0 is rejected with 422 validation.invalid_input."""

    def test_negative_on_create_returns_422(self, test_client: TestClient) -> None:
        """POST /definitions with default_best_before_days=-1 returns 422."""
        resp = test_client.post(
            "/api/definitions",
            json={"name": "Bad Shelf", "default_best_before_days": -1},
        )
        assert resp.status_code == 422

    def test_negative_on_create_error_code(self, test_client: TestClient) -> None:
        """Error code is validation.invalid_input for negative default_best_before_days."""
        resp = test_client.post(
            "/api/definitions",
            json={"name": "Bad Shelf", "default_best_before_days": -1},
        )
        body = resp.json()
        assert body["code"] == "validation.invalid_input"

    def test_negative_minus_ten_on_create_returns_422(self, test_client: TestClient) -> None:
        """POST /definitions with default_best_before_days=-10 returns 422."""
        resp = test_client.post(
            "/api/definitions",
            json={"name": "Very Bad Shelf", "default_best_before_days": -10},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_negative_on_patch_returns_422(self, test_client: TestClient) -> None:
        """PATCH /definitions/{id} with default_best_before_days=-1 returns 422."""
        defn = _create_definition(test_client, "Valid Item")
        resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"default_best_before_days": -5},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_zero_is_accepted_on_create(self, test_client: TestClient) -> None:
        """0 is ≥ 0 and must be accepted (same-day expiry semantics)."""
        data = _create_definition(test_client, "Same Day Zero", default_best_before_days=0)
        assert data["default_best_before_days"] == 0

    def test_zero_is_accepted_on_patch(self, test_client: TestClient) -> None:
        """PATCH with 0 is accepted."""
        defn = _create_definition(test_client, "Patch to Zero", default_best_before_days=7)
        resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"default_best_before_days": 0},
        )
        assert resp.status_code == 200
        assert resp.json()["default_best_before_days"] == 0


# ---------------------------------------------------------------------------
# 4. Service layer unit tests
# ---------------------------------------------------------------------------


class TestItemDefinitionServiceM3Step1:
    """ItemDefinitionService unit tests for M3 Step 1 logic.

    These use the ``db_session`` fixture which already seeds item_kinds
    (durable/consumable/perishable), so no separate kind setup is needed.
    """

    def test_create_default_best_before_days_none_when_omitted(self, db_session: Session) -> None:
        """Service.create without default_best_before_days stores None."""
        from app.schemas.item_definition import DefinitionCreate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        defn = svc.create(DefinitionCreate(name="Service Default"))
        db_session.commit()
        assert defn.default_best_before_days is None

    def test_create_stores_default_best_before_days(self, db_session: Session) -> None:
        """Service.create with default_best_before_days=7 stores 7 on the model."""
        from app.schemas.item_definition import DefinitionCreate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        defn = svc.create(DefinitionCreate(name="Service With Shelf", default_best_before_days=7))
        db_session.commit()
        assert defn.default_best_before_days == 7

    def test_create_stores_zero(self, db_session: Session) -> None:
        """Service.create with default_best_before_days=0 stores 0."""
        from app.schemas.item_definition import DefinitionCreate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        defn = svc.create(DefinitionCreate(name="Same Day Service", default_best_before_days=0))
        db_session.commit()
        assert defn.default_best_before_days == 0

    def test_update_sets_default_best_before_days(self, db_session: Session) -> None:
        """Service.update can set default_best_before_days."""
        from app.schemas.item_definition import DefinitionCreate, DefinitionUpdate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        defn = svc.create(DefinitionCreate(name="Update Test"))
        db_session.commit()

        updated = svc.update(defn.id, DefinitionUpdate(default_best_before_days=14))
        assert updated.default_best_before_days == 14

    def test_update_clears_default_best_before_days_to_none(self, db_session: Session) -> None:
        """Service.update with default_best_before_days=None (explicitly) clears it."""
        from app.schemas.item_definition import DefinitionCreate, DefinitionUpdate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        defn = svc.create(DefinitionCreate(name="Clearable Service", default_best_before_days=7))
        db_session.commit()

        # Explicitly send None in model_fields_set by constructing with keyword
        update_data = DefinitionUpdate(default_best_before_days=None)
        # Verify the field was explicitly set (not just defaulted to None)
        assert "default_best_before_days" in update_data.model_fields_set
        updated = svc.update(defn.id, update_data)
        assert updated.default_best_before_days is None

    def test_update_without_field_does_not_change_it(self, db_session: Session) -> None:
        """Service.update without default_best_before_days in payload leaves it intact."""
        from app.schemas.item_definition import DefinitionCreate, DefinitionUpdate
        from app.services.item_definition import ItemDefinitionService

        svc = ItemDefinitionService(db_session)
        defn = svc.create(DefinitionCreate(name="Preserved Service", default_best_before_days=30))
        db_session.commit()

        # Only change the name — default_best_before_days must be preserved
        updated = svc.update(defn.id, DefinitionUpdate(name="Preserved Service Renamed"))
        assert updated.default_best_before_days == 30

    def test_repository_stores_and_reads_back(self, db_session: Session) -> None:
        """Repository create/update thread default_best_before_days correctly."""
        from app.repositories.item_definition import ItemDefinitionRepository
        from app.repositories.item_kind import ItemKindRepository

        kind_repo = ItemKindRepository(db_session)
        durable = kind_repo.get_by_code("durable")
        assert durable is not None

        repo = ItemDefinitionRepository(db_session)
        defn = repo.create(
            name="Repo Test",
            kind_id=durable.id,
            default_best_before_days=21,
        )
        db_session.commit()

        # Read back to confirm persistence
        db_session.expire(defn)
        assert defn.default_best_before_days == 21

        # Update via repo
        updated = repo.update(
            defn,
            set_default_best_before_days=True,
            default_best_before_days=42,
        )
        db_session.commit()
        db_session.expire(updated)
        assert updated.default_best_before_days == 42

    def test_repository_clear_to_none_via_set_flag(self, db_session: Session) -> None:
        """Repository update with set_default_best_before_days=True and None clears it."""
        from app.repositories.item_definition import ItemDefinitionRepository
        from app.repositories.item_kind import ItemKindRepository

        kind_repo = ItemKindRepository(db_session)
        durable = kind_repo.get_by_code("durable")
        assert durable is not None

        repo = ItemDefinitionRepository(db_session)
        defn = repo.create(name="Repo Clear", kind_id=durable.id, default_best_before_days=7)
        db_session.commit()

        repo.update(defn, set_default_best_before_days=True, default_best_before_days=None)
        db_session.commit()
        db_session.expire(defn)
        assert defn.default_best_before_days is None

    def test_repository_update_without_flag_preserves_value(self, db_session: Session) -> None:
        """Repository update without set_default_best_before_days=True leaves the value alone."""
        from app.repositories.item_definition import ItemDefinitionRepository
        from app.repositories.item_kind import ItemKindRepository

        kind_repo = ItemKindRepository(db_session)
        durable = kind_repo.get_by_code("durable")
        assert durable is not None

        repo = ItemDefinitionRepository(db_session)
        defn = repo.create(name="Repo Preserve", kind_id=durable.id, default_best_before_days=5)
        db_session.commit()

        # Update name only — flag is False (default) so value must not change
        repo.update(defn, name="Repo Preserve Renamed")
        db_session.commit()
        db_session.expire(defn)
        assert defn.default_best_before_days == 5


# ---------------------------------------------------------------------------
# 5. Alembic migration 0013
# ---------------------------------------------------------------------------


class TestAlembicMigration0013:
    """Migration 0013 must upgrade from 0012 and downgrade cleanly."""

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

    def test_upgrade_0013_adds_column(self) -> None:
        """Upgrading to 0013 adds default_best_before_days to item_definitions."""
        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "0013", url=url)
            assert rc == 0, f"alembic upgrade 0013 failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols_result = conn.execute(text("PRAGMA table_info(item_definitions)")).fetchall()
                col_names = {row[1] for row in cols_result}
                assert "default_best_before_days" in col_names, (
                    f"default_best_before_days missing; columns: {col_names}"
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0013_column_is_nullable(self) -> None:
        """After upgrade 0013, inserting a row without the column leaves it NULL."""
        url, db_path = _make_temp_db_url()
        try:
            # Upgrade to 0012 first to create the item_definitions row structure
            rc12, out12 = self._run_alembic("upgrade", "0012", url=url)
            assert rc12 == 0, f"upgrade 0012 failed:\n{out12}"

            # Insert a definition row without default_best_before_days
            engine = create_engine(url)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO item_definitions (name, kind_id, unit, stock_tracking_mode) "
                        "VALUES ('Pre-existing', 1, 'pcs', 'exact')"
                    )
                )

            # Now upgrade to 0013
            rc13, out13 = self._run_alembic("upgrade", "0013", url=url)
            assert rc13 == 0, f"upgrade 0013 failed:\n{out13}"

            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT default_best_before_days FROM item_definitions "
                        "WHERE name = 'Pre-existing'"
                    )
                ).fetchone()
                assert row is not None
                assert row[0] is None, f"Expected NULL default_best_before_days, got {row[0]!r}"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_0013_drops_column(self) -> None:
        """Downgrading from 0013 to 0012 drops default_best_before_days."""
        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "0013", url=url)
            assert rc_up == 0, f"upgrade 0013 failed:\n{out_up}"

            rc_down, out_down = self._run_alembic("downgrade", "0012", url=url)
            assert rc_down == 0, f"downgrade to 0012 failed:\n{out_down}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols_result = conn.execute(text("PRAGMA table_info(item_definitions)")).fetchall()
                col_names = {row[1] for row in cols_result}
                assert "default_best_before_days" not in col_names, (
                    "default_best_before_days still present after downgrade to 0012"
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_head_includes_0013(self) -> None:
        """Upgrading to head includes the 0013 column."""
        url, db_path = _make_temp_db_url()
        try:
            rc, out = self._run_alembic("upgrade", "head", url=url)
            assert rc == 0, f"upgrade head failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols_result = conn.execute(text("PRAGMA table_info(item_definitions)")).fetchall()
                col_names = {row[1] for row in cols_result}
                assert "default_best_before_days" in col_names, (
                    f"default_best_before_days missing after upgrade head; columns: {col_names}"
                )
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_stepwise_upgrade_0012_to_0013(self) -> None:
        """Stepwise upgrade from 0012 to 0013 is clean."""
        url, db_path = _make_temp_db_url()
        try:
            rc12, out12 = self._run_alembic("upgrade", "0012", url=url)
            assert rc12 == 0, f"upgrade 0012 failed:\n{out12}"

            rc13, out13 = self._run_alembic("upgrade", "0013", url=url)
            assert rc13 == 0, f"upgrade 0013 failed:\n{out13}"

            engine = create_engine(url)
            with engine.connect() as conn:
                cols_result = conn.execute(text("PRAGMA table_info(item_definitions)")).fetchall()
                col_names = {row[1] for row in cols_result}
                assert "default_best_before_days" in col_names
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_full_roundtrip_upgrade_head_downgrade_base(self) -> None:
        """Full upgrade to head then downgrade to base is clean after adding 0013."""
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
