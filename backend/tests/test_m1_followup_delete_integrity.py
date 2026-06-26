"""M1 followup — delete-integrity fixes.

Coverage:
Fix 1 — Definition delete-guard (HTTP 409 when instances exist):
  - DELETE a definition with ≥1 referencing instance → 409 (clear detail).
  - DELETE a definition with no instances → 204 (unchanged behaviour).
  - Service-layer: delete() raises 409 when instances exist.
  - StockInstanceRepository.has_instances_for_definition() returns True/False.

Fix 2 — SQLite FK enforcement:
  - PRAGMA foreign_keys is ON for SQLite connections created by get_engine().
  - Inserting a stock instance with a non-existent definition_id raises
    IntegrityError at the DB layer.
  - Inserting a stock instance with a non-existent location_id raises
    IntegrityError at the DB layer.
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
    """Return (url, path) for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m1_delinteg_")
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
    """Temp-file SQLite DB; patches env vars so get_engine() uses it."""
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m1-delinteg")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def test_client(temp_db: Path) -> Generator[TestClient]:  # noqa: ARG001
    """TestClient with full schema and an authenticated admin session."""
    import importlib

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.attachment as attachment_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.media_file as media_file_mod
    import app.models.session as sess_mod
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as stock_movement_mod
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
    importlib.reload(stock_movement_mod)
    importlib.reload(loc_mod)
    importlib.reload(media_file_mod)
    importlib.reload(attachment_mod)

    from app.db.base import Base, get_engine
    from app.main import create_app

    engine = get_engine()
    Base.metadata.create_all(engine)
    app = create_app()

    with TestClient(app, raise_server_exceptions=True) as client:
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            from app.auth.passwords import hash_password
            from app.models.item_kind import ItemKind
            from app.repositories.user import UserRepository

            repo = UserRepository(db)
            repo.create(
                email="admin@example.com",
                password_hash=hash_password("adminpass"),
            )
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

        response = client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "adminpass"},
        )
        assert response.status_code == 200
        yield client

    drop_all_sqlite(Base, engine)


@pytest.fixture()
def db_session() -> Generator[Session]:
    """In-memory SQLite session with FK enforcement ON and all models registered."""
    import importlib

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.attachment as attachment_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.media_file as media_file_mod
    import app.models.session as sess_mod
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as stock_movement_mod
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
    importlib.reload(stock_movement_mod)
    importlib.reload(loc_mod)
    importlib.reload(media_file_mod)
    importlib.reload(attachment_mod)

    # Use the app's engine factory (which now enables FK enforcement).
    from sqlalchemy import event as sa_event

    from app.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    # Manually enable FK enforcement for this in-memory engine.
    @sa_event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn: object, _: object) -> None:
        import sqlite3

        if isinstance(dbapi_conn, sqlite3.Connection):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()

    # Seed a minimal set: one kind.
    from app.models.item_kind import ItemKind

    kind = ItemKind(code="durable", name="Durable", is_system=True)
    session.add(kind)
    session.commit()

    try:
        yield session
    finally:
        session.close()
        drop_all_sqlite(Base, engine)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _create_location(client: TestClient, name: str) -> dict:  # type: ignore[type-arg]
    resp = client.post("/api/locations", json={"name": name})
    assert resp.status_code == 201
    return resp.json()  # type: ignore[return-value]


def _create_definition(client: TestClient, name: str) -> dict:  # type: ignore[type-arg]
    resp = client.post("/api/definitions", json={"name": name})
    assert resp.status_code == 201
    return resp.json()  # type: ignore[return-value]


def _create_instance(
    client: TestClient,
    definition_id: int,
    *,
    location_id: int | None = None,
    expect_status: int = 201,
) -> dict:  # type: ignore[type-arg]
    payload: dict = {"definition_id": definition_id}  # type: ignore[type-arg]
    if location_id is not None:
        payload["location_id"] = location_id
    resp = client.post("/api/instances", json=payload)
    assert resp.status_code == expect_status
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Fix 1: Definition delete-guard (HTTP tests)
# ---------------------------------------------------------------------------


class TestDefinitionDeleteGuard:
    """Definition cannot be deleted while instances reference it (HTTP 409)."""

    def test_delete_definition_with_no_instances_returns_204(self, test_client: TestClient) -> None:
        """DELETE /definitions/{id} with no referencing instances → 204."""
        defn = _create_definition(test_client, "Empty Definition")
        resp = test_client.delete(f"/api/definitions/{defn['id']}")
        assert resp.status_code == 204

    def test_delete_definition_with_one_instance_returns_409(self, test_client: TestClient) -> None:
        """DELETE /definitions/{id} when one instance exists → 409."""
        defn = _create_definition(test_client, "Drill")
        _create_instance(test_client, defn["id"])
        resp = test_client.delete(f"/api/definitions/{defn['id']}")
        assert resp.status_code == 409

    def test_delete_definition_with_multiple_instances_returns_409(
        self, test_client: TestClient
    ) -> None:
        """DELETE /definitions/{id} when multiple instances exist → 409."""
        defn = _create_definition(test_client, "Hammer")
        _create_instance(test_client, defn["id"])
        _create_instance(test_client, defn["id"])
        resp = test_client.delete(f"/api/definitions/{defn['id']}")
        assert resp.status_code == 409

    def test_delete_definition_409_detail_mentions_instances(self, test_client: TestClient) -> None:
        """The 409 error code indicates item definition has instances."""
        defn = _create_definition(test_client, "Saw")
        _create_instance(test_client, defn["id"])
        resp = test_client.delete(f"/api/definitions/{defn['id']}")
        assert resp.status_code == 409
        body = resp.json()
        # Should use the new uniform error envelope with stable code.
        assert body.get("code") == "item_definition.has_instances"
        assert "instance" in body.get("message", "").lower()

    def test_delete_definition_after_instance_deleted_returns_204(
        self, test_client: TestClient
    ) -> None:
        """After the last instance is deleted, the definition becomes deletable."""
        defn = _create_definition(test_client, "Wrench")
        inst = _create_instance(test_client, defn["id"])

        # First: definition is blocked.
        resp = test_client.delete(f"/api/definitions/{defn['id']}")
        assert resp.status_code == 409

        # Delete the instance.
        del_resp = test_client.delete(f"/api/instances/{inst['id']}")
        assert del_resp.status_code == 204

        # Now the definition can be deleted.
        resp2 = test_client.delete(f"/api/definitions/{defn['id']}")
        assert resp2.status_code == 204

    def test_delete_instance_of_one_definition_does_not_block_another(
        self, test_client: TestClient
    ) -> None:
        """Instances for definition A do not block deletion of definition B."""
        defn_a = _create_definition(test_client, "Definition A")
        defn_b = _create_definition(test_client, "Definition B")
        _create_instance(test_client, defn_a["id"])

        # defn_b has no instances — must be deletable.
        resp = test_client.delete(f"/api/definitions/{defn_b['id']}")
        assert resp.status_code == 204

    def test_delete_nonexistent_definition_returns_404(self, test_client: TestClient) -> None:
        """DELETE /definitions/9999 → 404 (unchanged)."""
        resp = test_client.delete("/api/definitions/9999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Fix 1: Repository method unit tests
# ---------------------------------------------------------------------------


class TestHasInstancesForDefinition:
    """StockInstanceRepository.has_instances_for_definition unit tests."""

    def test_returns_false_when_no_instances(self, db_session: Session) -> None:
        """has_instances_for_definition returns False when no instances exist."""
        from app.models.item_definition import ItemDefinition
        from app.repositories.stock_instance import StockInstanceRepository

        kind_id = db_session.execute(text("SELECT id FROM item_kinds LIMIT 1")).scalar()
        defn = ItemDefinition(name="Empty", kind_id=kind_id)
        db_session.add(defn)
        db_session.flush()

        repo = StockInstanceRepository(db_session)
        assert repo.has_instances_for_definition(defn.id) is False

    def test_returns_true_when_instance_exists(self, db_session: Session) -> None:
        """has_instances_for_definition returns True when at least one instance exists."""
        from app.models.item_definition import ItemDefinition
        from app.models.stock_instance import StockInstance
        from app.repositories.stock_instance import StockInstanceRepository

        kind_id = db_session.execute(text("SELECT id FROM item_kinds LIMIT 1")).scalar()
        defn = ItemDefinition(name="HasInstance", kind_id=kind_id)
        db_session.add(defn)
        db_session.flush()

        inst = StockInstance(definition_id=defn.id)
        db_session.add(inst)
        db_session.flush()

        repo = StockInstanceRepository(db_session)
        assert repo.has_instances_for_definition(defn.id) is True

    def test_only_checks_target_definition(self, db_session: Session) -> None:
        """has_instances_for_definition is scoped to the given definition_id."""
        from app.models.item_definition import ItemDefinition
        from app.models.stock_instance import StockInstance
        from app.repositories.stock_instance import StockInstanceRepository

        kind_id = db_session.execute(text("SELECT id FROM item_kinds LIMIT 1")).scalar()
        defn_a = ItemDefinition(name="A", kind_id=kind_id)
        defn_b = ItemDefinition(name="B", kind_id=kind_id)
        db_session.add_all([defn_a, defn_b])
        db_session.flush()

        # Only defn_a gets an instance.
        db_session.add(StockInstance(definition_id=defn_a.id))
        db_session.flush()

        repo = StockInstanceRepository(db_session)
        assert repo.has_instances_for_definition(defn_a.id) is True
        assert repo.has_instances_for_definition(defn_b.id) is False


# ---------------------------------------------------------------------------
# Fix 1: Service-layer unit tests
# ---------------------------------------------------------------------------


class TestDefinitionDeleteServiceGuard:
    """ItemDefinitionService.delete() raises 409 when instances exist."""

    def test_service_delete_409_when_instance_exists(self, db_session: Session) -> None:
        """Service.delete raises AppError 409 when the definition has instances."""
        from app.core.errors import AppError, ErrorCode
        from app.models.item_definition import ItemDefinition
        from app.models.stock_instance import StockInstance
        from app.services.item_definition import ItemDefinitionService

        kind_id = db_session.execute(text("SELECT id FROM item_kinds LIMIT 1")).scalar()
        defn = ItemDefinition(name="Blocked", kind_id=kind_id)
        db_session.add(defn)
        db_session.flush()
        db_session.add(StockInstance(definition_id=defn.id))
        db_session.commit()

        svc = ItemDefinitionService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.delete(defn.id)
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == ErrorCode.ITEM_DEFINITION_HAS_INSTANCES

    def test_service_delete_succeeds_when_no_instances(self, db_session: Session) -> None:
        """Service.delete succeeds (no error) when no instances reference the definition."""
        from app.models.item_definition import ItemDefinition
        from app.repositories.item_definition import ItemDefinitionRepository
        from app.services.item_definition import ItemDefinitionService

        kind_id = db_session.execute(text("SELECT id FROM item_kinds LIMIT 1")).scalar()
        defn = ItemDefinition(name="Empty", kind_id=kind_id)
        db_session.add(defn)
        db_session.commit()
        defn_id = defn.id  # capture id before delete

        svc = ItemDefinitionService(db_session)
        svc.delete(defn_id)  # must not raise
        db_session.commit()

        # Row should be gone.
        assert ItemDefinitionRepository(db_session).get(defn_id) is None


# ---------------------------------------------------------------------------
# Fix 2: SQLite FK enforcement
# ---------------------------------------------------------------------------


class TestSQLiteFKEnforcement:
    """FK enforcement is active for SQLite connections created by get_engine()."""

    def test_pragma_foreign_keys_is_on(self, temp_db: Path) -> None:
        """PRAGMA foreign_keys returns 1 for a connection from get_engine()."""
        from app.db.base import get_engine

        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA foreign_keys")).scalar()
        assert result == 1, f"Expected PRAGMA foreign_keys=1, got {result}"

    def test_orphaned_instance_insert_raises_integrity_error(self, db_session: Session) -> None:
        """Inserting an instance with a non-existent definition_id raises IntegrityError."""
        from sqlalchemy.exc import IntegrityError

        from app.models.stock_instance import StockInstance

        with pytest.raises(IntegrityError):
            db_session.add(StockInstance(definition_id=99999))
            db_session.flush()

    def test_instance_with_nonexistent_location_raises_integrity_error(
        self, db_session: Session
    ) -> None:
        """Inserting an instance with a non-existent location_id raises IntegrityError."""
        from sqlalchemy.exc import IntegrityError

        from app.models.item_definition import ItemDefinition
        from app.models.stock_instance import StockInstance

        kind_id = db_session.execute(text("SELECT id FROM item_kinds LIMIT 1")).scalar()
        defn = ItemDefinition(name="Widget", kind_id=kind_id)
        db_session.add(defn)
        db_session.flush()

        with pytest.raises(IntegrityError):
            db_session.add(StockInstance(definition_id=defn.id, location_id=99999))
            db_session.flush()

    def test_get_engine_fk_enforcement_via_http(self, test_client: TestClient) -> None:
        """FK enforcement is active in the engine used by the real app."""
        # Verify via PRAGMA query through a raw connection on the app's engine.
        from app.db.base import get_engine

        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA foreign_keys")).scalar()
        assert result == 1, f"Expected PRAGMA foreign_keys=1, got {result}"
