"""M1 Step 1 tests: Location self-referential tree.

Required coverage (easy-to-get-wrong logic, per M1.md §5 / §9 Step 1):
- Cycle rejected: reparent a node under itself (409).
- Cycle rejected: reparent a node under one of its descendants (409).
- Valid reparent succeeds (200).
- Delete-guard: deleting a non-empty node returns 409.
- Deleting a leaf node returns 204.
- /locations/tree DTO shape correct (nested children, no orphans).
- q= case-insensitive substring search on name.
- parent_id filter returns only children of that node.
- Migration 0004 upgrade clean on an empty DB.
- Migration 0004 downgrade clean.

Also tests:
- Basic CRUD via HTTP (create, get, update, delete).
- 404 for missing location.
- Service-layer cycle checks (unit tested directly, not just HTTP).
"""

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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m1step1_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()  # Start empty.
    return f"sqlite:///{path_str}", path


def _make_fresh_session() -> Session:
    """In-memory SQLite session with all current models registered.

    Reloads model modules to avoid stale metadata from other test modules.
    """
    import importlib

    from sqlalchemy import event

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.attachment as attachment_mod
    import app.models.audit_log as audit_log_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.media_file as media_file_mod
    import app.models.note as note_mod
    import app.models.session as sess_mod
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as stock_movement_mod
    import app.models.tag as tag_mod
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
    importlib.reload(tag_mod)
    importlib.reload(note_mod)
    importlib.reload(audit_log_mod)

    from app.db.base import Base as _Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enforce_fk(dbapi_conn: object, _: object) -> None:  # type: ignore[type-arg]
        import sqlite3

        if isinstance(dbapi_conn, sqlite3.Connection):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    _Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return factory()


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
def db_session() -> Generator[Session]:
    """Fresh in-memory SQLite session for unit tests."""
    session = _make_fresh_session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite; sets SECRET_KEY, ENVIRONMENT=test, DATABASE_URL."""
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-m1-step1")
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
    import app.models.attachment as attachment_mod
    import app.models.audit_log as audit_log_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.media_file as media_file_mod
    import app.models.note as note_mod
    import app.models.session as sess_mod
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as stock_movement_mod
    import app.models.tag as tag_mod
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
    importlib.reload(tag_mod)
    importlib.reload(note_mod)
    importlib.reload(audit_log_mod)

    from app.db.base import Base, get_engine
    from app.main import create_app

    engine = get_engine()
    Base.metadata.create_all(engine)
    app = create_app()

    with TestClient(app, raise_server_exceptions=True) as client:
        # Create an admin user and log in so all authenticated endpoints work.
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            from app.auth.passwords import hash_password
            from app.repositories.user import UserRepository

            repo = UserRepository(db)
            repo.create(email="admin@example.com", password_hash=hash_password("adminpass"))
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


# ---------------------------------------------------------------------------
# Helper: create a location via HTTP
# ---------------------------------------------------------------------------


def _create_location(
    client: TestClient,
    name: str,
    parent_id: int | None = None,
    description: str | None = None,
) -> dict:  # type: ignore[type-arg]
    """POST /api/locations and return the response JSON dict."""
    payload: dict = {"name": name}  # type: ignore[type-arg]
    if parent_id is not None:
        payload["parent_id"] = parent_id
    if description is not None:
        payload["description"] = description

    resp = client.post("/api/locations", json=payload)
    assert resp.status_code == 201, f"create_location failed: {resp.status_code} {resp.json()}"
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. Basic CRUD
# ---------------------------------------------------------------------------


class TestLocationCRUD:
    """Basic CRUD operations."""

    def test_create_root_location(self, test_client: TestClient) -> None:
        """POST /locations creates a root-level location (parent_id=null)."""
        data = _create_location(test_client, "Home")
        assert data["name"] == "Home"
        assert data["parent_id"] is None
        assert "id" in data
        assert "created_at" in data

    def test_create_child_location(self, test_client: TestClient) -> None:
        """POST /locations with parent_id creates a child location."""
        home = _create_location(test_client, "Home")
        garage = _create_location(test_client, "Garage", parent_id=home["id"])
        assert garage["parent_id"] == home["id"]

    def test_get_location_by_id(self, test_client: TestClient) -> None:
        """GET /locations/{id} returns the location."""
        home = _create_location(test_client, "Home")
        resp = test_client.get(f"/api/locations/{home['id']}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Home"

    def test_get_location_404(self, test_client: TestClient) -> None:
        """GET /locations/{id} returns 404 for a non-existent id."""
        resp = test_client.get("/api/locations/9999")
        assert resp.status_code == 404

    def test_list_locations_empty(self, test_client: TestClient) -> None:
        """GET /locations returns an empty list when no locations exist."""
        resp = test_client.get("/api/locations")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_locations_returns_all(self, test_client: TestClient) -> None:
        """GET /locations returns all locations (flat)."""
        _create_location(test_client, "A")
        _create_location(test_client, "B")
        resp = test_client.get("/api/locations")
        assert resp.status_code == 200
        names = {loc["name"] for loc in resp.json()}
        assert names == {"A", "B"}

    def test_update_name(self, test_client: TestClient) -> None:
        """PATCH /locations/{id} can update the name."""
        home = _create_location(test_client, "Old Name")
        resp = test_client.patch(f"/api/locations/{home['id']}", json={"name": "New Name"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"

    def test_update_description(self, test_client: TestClient) -> None:
        """PATCH /locations/{id} can update the description."""
        home = _create_location(test_client, "Home")
        resp = test_client.patch(f"/api/locations/{home['id']}", json={"description": "My home"})
        assert resp.status_code == 200
        assert resp.json()["description"] == "My home"

    def test_delete_leaf_location(self, test_client: TestClient) -> None:
        """DELETE /locations/{id} on a leaf returns 204."""
        home = _create_location(test_client, "Home")
        resp = test_client.delete(f"/api/locations/{home['id']}")
        assert resp.status_code == 204

        # Confirm it's gone.
        get_resp = test_client.get(f"/api/locations/{home['id']}")
        assert get_resp.status_code == 404

    def test_delete_404_for_nonexistent(self, test_client: TestClient) -> None:
        """DELETE /locations/{id} returns 404 for a non-existent location."""
        resp = test_client.delete("/api/locations/9999")
        assert resp.status_code == 404

    def test_create_requires_auth(self, temp_db: Path) -> None:  # noqa: ARG002
        """POST /locations without a session returns 401."""
        from app.db.base import Base, get_engine
        from app.main import create_app

        engine = get_engine()
        Base.metadata.create_all(engine)
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post("/api/locations", json={"name": "No Auth"})
            assert resp.status_code == 401
        drop_all_sqlite(Base, engine)


# ---------------------------------------------------------------------------
# 2. Cycle prevention (easy-to-get-wrong)
# ---------------------------------------------------------------------------


class TestCyclePrevention:
    """Cycle prevention — easy-to-get-wrong logic (service layer, not SQL)."""

    def test_reparent_under_self_is_rejected(self, test_client: TestClient) -> None:
        """PATCH /locations/{id} with parent_id == id returns 409."""
        node = _create_location(test_client, "A")
        resp = test_client.patch(f"/api/locations/{node['id']}", json={"parent_id": node["id"]})
        assert resp.status_code == 409
        assert resp.json()["code"] == "tree.cycle"

    def test_reparent_under_direct_child_is_rejected(self, test_client: TestClient) -> None:
        """Parent → Child: reparenting Parent under Child is rejected (cycle)."""
        parent = _create_location(test_client, "Parent")
        child = _create_location(test_client, "Child", parent_id=parent["id"])

        resp = test_client.patch(f"/api/locations/{parent['id']}", json={"parent_id": child["id"]})
        assert resp.status_code == 409
        assert resp.json()["code"] == "tree.cycle"

    def test_reparent_under_distant_descendant_is_rejected(self, test_client: TestClient) -> None:
        """A → B → C: reparenting A under C (deep descendant) is rejected."""
        a = _create_location(test_client, "A")
        b = _create_location(test_client, "B", parent_id=a["id"])
        c = _create_location(test_client, "C", parent_id=b["id"])

        resp = test_client.patch(f"/api/locations/{a['id']}", json={"parent_id": c["id"]})
        assert resp.status_code == 409
        assert resp.json()["code"] == "tree.cycle"

    def test_valid_reparent_succeeds(self, test_client: TestClient) -> None:
        """Reparenting a node to a valid (non-descendant) node succeeds."""
        home = _create_location(test_client, "Home")
        work = _create_location(test_client, "Work")
        garage = _create_location(test_client, "Garage", parent_id=home["id"])

        # Reparent Garage from Home to Work — valid.
        resp = test_client.patch(f"/api/locations/{garage['id']}", json={"parent_id": work["id"]})
        assert resp.status_code == 200
        assert resp.json()["parent_id"] == work["id"]

    def test_reparent_to_root_succeeds(self, test_client: TestClient) -> None:
        """Reparenting a node to null (root) succeeds."""
        home = _create_location(test_client, "Home")
        garage = _create_location(test_client, "Garage", parent_id=home["id"])

        resp = test_client.patch(f"/api/locations/{garage['id']}", json={"parent_id": None})
        assert resp.status_code == 200
        assert resp.json()["parent_id"] is None

    def test_service_cycle_check_self(self, db_session: Session) -> None:
        """LocationService._assert_no_cycle raises AppError 409 on self-reference (unit test)."""
        from app.core.errors import AppError, ErrorCode
        from app.repositories.location import LocationRepository
        from app.services.location import LocationService

        repo = LocationRepository(db_session)
        loc = repo.create(name="A")
        db_session.commit()

        svc = LocationService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc._assert_no_cycle(loc.id, loc.id)
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == ErrorCode.TREE_CYCLE

    def test_service_cycle_check_descendant(self, db_session: Session) -> None:
        """LocationService._assert_no_cycle raises AppError 409 for a descendant parent."""
        from app.core.errors import AppError, ErrorCode
        from app.repositories.location import LocationRepository
        from app.services.location import LocationService

        repo = LocationRepository(db_session)
        a = repo.create(name="A")
        db_session.flush()
        b = repo.create(name="B", parent_id=a.id)
        db_session.flush()
        c = repo.create(name="C", parent_id=b.id)
        db_session.commit()

        svc = LocationService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc._assert_no_cycle(a.id, c.id)
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == ErrorCode.TREE_CYCLE


# ---------------------------------------------------------------------------
# 3. Delete guard (easy-to-get-wrong)
# ---------------------------------------------------------------------------


class TestDeleteGuard:
    """Delete-guard — non-empty node must return 409."""

    def test_delete_non_empty_node_returns_409(self, test_client: TestClient) -> None:
        """DELETE /locations/{id} on a node with children returns 409."""
        parent = _create_location(test_client, "Parent")
        _create_location(test_client, "Child", parent_id=parent["id"])

        resp = test_client.delete(f"/api/locations/{parent['id']}")
        assert resp.status_code == 409
        assert resp.json()["code"] == "tree.delete_has_children"

    def test_delete_becomes_allowed_after_child_removed(self, test_client: TestClient) -> None:
        """After the child is deleted, the parent can be deleted too."""
        parent = _create_location(test_client, "Parent")
        child = _create_location(test_client, "Child", parent_id=parent["id"])

        # Delete child first.
        resp_child = test_client.delete(f"/api/locations/{child['id']}")
        assert resp_child.status_code == 204

        # Now parent should be deletable.
        resp_parent = test_client.delete(f"/api/locations/{parent['id']}")
        assert resp_parent.status_code == 204

    def test_service_delete_guard_unit(self, db_session: Session) -> None:
        """LocationService.delete raises AppError 409 (via _assert_deletable) for a non-empty node."""
        from app.core.errors import AppError, ErrorCode
        from app.repositories.location import LocationRepository
        from app.services.location import LocationService

        repo = LocationRepository(db_session)
        parent = repo.create(name="Parent")
        db_session.flush()
        repo.create(name="Child", parent_id=parent.id)
        db_session.commit()

        svc = LocationService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.delete(parent.id)
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == ErrorCode.TREE_DELETE_HAS_CHILDREN


# ---------------------------------------------------------------------------
# 4. /locations/tree DTO shape
# ---------------------------------------------------------------------------


class TestTreeShape:
    """GET /locations/tree — DTO shape and nesting correctness."""

    def test_tree_empty(self, test_client: TestClient) -> None:
        """GET /locations/tree returns [] when no locations exist."""
        resp = test_client.get("/api/locations/tree")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_tree_flat_roots(self, test_client: TestClient) -> None:
        """All root locations appear as top-level nodes (no children)."""
        _create_location(test_client, "A")
        _create_location(test_client, "B")
        resp = test_client.get("/api/locations/tree")
        assert resp.status_code == 200
        tree = resp.json()
        assert len(tree) == 2
        names = {n["name"] for n in tree}
        assert names == {"A", "B"}
        for node in tree:
            assert node["children"] == []

    def test_tree_nested_shape(self, test_client: TestClient) -> None:
        """Home → Garage → Toolbox is correctly nested in the tree."""
        home = _create_location(test_client, "Home")
        garage = _create_location(test_client, "Garage", parent_id=home["id"])
        toolbox = _create_location(test_client, "Toolbox", parent_id=garage["id"])

        resp = test_client.get("/api/locations/tree")
        assert resp.status_code == 200
        tree = resp.json()

        # Exactly one root.
        assert len(tree) == 1
        root = tree[0]
        assert root["name"] == "Home"
        assert root["id"] == home["id"]

        # One child of Home: Garage.
        assert len(root["children"]) == 1
        garage_node = root["children"][0]
        assert garage_node["name"] == "Garage"
        assert garage_node["id"] == garage["id"]

        # One child of Garage: Toolbox.
        assert len(garage_node["children"]) == 1
        toolbox_node = garage_node["children"][0]
        assert toolbox_node["name"] == "Toolbox"
        assert toolbox_node["id"] == toolbox["id"]
        assert toolbox_node["children"] == []

    def test_tree_node_has_required_fields(self, test_client: TestClient) -> None:
        """Each tree node has id, name, description, parent_id, created_at, children."""
        _create_location(test_client, "Root", description="Root location")
        tree = test_client.get("/api/locations/tree").json()
        node = tree[0]
        assert "id" in node
        assert "name" in node
        assert "description" in node
        assert "parent_id" in node
        assert "created_at" in node
        assert "children" in node


# ---------------------------------------------------------------------------
# 5. Search (q param)
# ---------------------------------------------------------------------------


class TestSearch:
    """q= case-insensitive substring search."""

    def test_search_case_insensitive(self, test_client: TestClient) -> None:
        """q=garage matches 'Garage' regardless of case."""
        _create_location(test_client, "Garage")
        _create_location(test_client, "Kitchen")

        for q in ["garage", "GARAGE", "Garage", "arag"]:
            resp = test_client.get(f"/api/locations?q={q}")
            assert resp.status_code == 200
            results = resp.json()
            assert len(results) == 1, f"Expected 1 result for q={q!r}, got {results}"
            assert results[0]["name"] == "Garage"

    def test_search_no_match(self, test_client: TestClient) -> None:
        """q= with no matching locations returns []."""
        _create_location(test_client, "Garage")
        resp = test_client.get("/api/locations?q=xyz_no_match")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_search_matches_multiple(self, test_client: TestClient) -> None:
        """Substring match can return multiple results."""
        _create_location(test_client, "Storage Room")
        _create_location(test_client, "Storage Closet")
        _create_location(test_client, "Garage")

        resp = test_client.get("/api/locations?q=storage")
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 2
        names = {r["name"] for r in results}
        assert names == {"Storage Room", "Storage Closet"}

    def test_search_empty_q_returns_all(self, test_client: TestClient) -> None:
        """No q param returns all locations."""
        _create_location(test_client, "A")
        _create_location(test_client, "B")
        resp = test_client.get("/api/locations")
        assert resp.status_code == 200
        assert len(resp.json()) == 2


# ---------------------------------------------------------------------------
# 6. parent_id filter
# ---------------------------------------------------------------------------


class TestParentIdFilter:
    """parent_id= filter returns only children of that node."""

    def test_filter_by_parent_id(self, test_client: TestClient) -> None:
        """GET /locations?parent_id=X returns only direct children of X."""
        home = _create_location(test_client, "Home")
        work = _create_location(test_client, "Work")
        _create_location(test_client, "Garage", parent_id=home["id"])
        _create_location(test_client, "Living Room", parent_id=home["id"])
        _create_location(test_client, "Office", parent_id=work["id"])

        resp = test_client.get(f"/api/locations?parent_id={home['id']}")
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 2
        names = {r["name"] for r in results}
        assert names == {"Garage", "Living Room"}

    def test_filter_by_parent_id_no_children(self, test_client: TestClient) -> None:
        """GET /locations?parent_id=X returns [] when X has no children."""
        leaf = _create_location(test_client, "Leaf")
        resp = test_client.get(f"/api/locations?parent_id={leaf['id']}")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# 7. Alembic migration 0004
# ---------------------------------------------------------------------------


class TestAlembicMigration0004:
    """Migration 0004 must apply cleanly on an empty DB and downgrade cleanly."""

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

    def test_upgrade_head_creates_locations_table(self) -> None:
        """alembic upgrade head creates the locations table."""
        url, db_path = _make_temp_db_url()
        try:
            rc, output = self._run_alembic("upgrade", "head", url=url)
            assert rc == 0, f"alembic upgrade head failed:\n{output}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "locations" in table_names, f"locations table missing; found: {table_names}"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0004_clean(self) -> None:
        """Stepwise upgrade 0001→0002→0003→0004 is clean."""
        url, db_path = _make_temp_db_url()
        try:
            for rev in ["0001", "0002", "0003", "0004"]:
                rc, out = self._run_alembic("upgrade", rev, url=url)
                assert rc == 0, f"alembic upgrade {rev} failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "locations" in table_names
                assert "users" in table_names
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_0004_drops_locations(self) -> None:
        """Downgrading from 0004 to 0003 drops locations, keeps other tables."""
        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "head", url=url)
            assert rc_up == 0, f"alembic upgrade failed:\n{out_up}"

            rc_down, out_down = self._run_alembic("downgrade", "0003", url=url)
            assert rc_down == 0, f"alembic downgrade to 0003 failed:\n{out_down}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "locations" not in table_names, (
                    "locations must be dropped after downgrade to 0003"
                )
                assert "app_config" in table_names, "app_config must still exist at 0003"
                assert "users" in table_names
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_base_is_clean(self) -> None:
        """alembic downgrade base removes all application tables."""
        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "head", url=url)
            assert rc_up == 0, f"alembic upgrade failed:\n{out_up}"

            rc_down, out_down = self._run_alembic("downgrade", "base", url=url)
            assert rc_down == 0, f"alembic downgrade base failed:\n{out_down}"

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


# ---------------------------------------------------------------------------
# 8. Repository layer unit tests
# ---------------------------------------------------------------------------


class TestLocationRepository:
    """LocationRepository unit tests (directly, no HTTP)."""

    def test_create_and_get(self, db_session: Session) -> None:
        """create() and get() roundtrip."""
        from app.repositories.location import LocationRepository

        repo = LocationRepository(db_session)
        loc = repo.create(name="Test", description="Desc")
        db_session.commit()

        found = repo.get(loc.id)
        assert found is not None
        assert found.name == "Test"
        assert found.description == "Desc"

    def test_get_returns_none_for_missing(self, db_session: Session) -> None:
        """get() returns None for a non-existent id."""
        from app.repositories.location import LocationRepository

        repo = LocationRepository(db_session)
        assert repo.get(9999) is None

    def test_has_children_false_for_leaf(self, db_session: Session) -> None:
        """has_children() is False for a leaf node."""
        from app.repositories.location import LocationRepository

        repo = LocationRepository(db_session)
        loc = repo.create(name="Leaf")
        db_session.commit()
        assert repo.has_children(loc.id) is False

    def test_has_children_true_for_parent(self, db_session: Session) -> None:
        """has_children() is True when the node has a child."""
        from app.repositories.location import LocationRepository

        repo = LocationRepository(db_session)
        parent = repo.create(name="Parent")
        db_session.flush()
        repo.create(name="Child", parent_id=parent.id)
        db_session.commit()
        assert repo.has_children(parent.id) is True

    def test_get_descendants_empty_for_leaf(self, db_session: Session) -> None:
        """get_descendants() returns [] for a leaf node."""
        from app.repositories.location import LocationRepository

        repo = LocationRepository(db_session)
        leaf = repo.create(name="Leaf")
        db_session.commit()
        assert repo.get_descendants(leaf.id) == []

    def test_get_descendants_multi_level(self, db_session: Session) -> None:
        """get_descendants() returns all descendants across multiple levels."""
        from app.repositories.location import LocationRepository

        repo = LocationRepository(db_session)
        a = repo.create(name="A")
        db_session.flush()
        b = repo.create(name="B", parent_id=a.id)
        db_session.flush()
        c = repo.create(name="C", parent_id=b.id)
        db_session.flush()
        d = repo.create(name="D", parent_id=a.id)  # second child of A
        db_session.commit()

        descendants = repo.get_descendants(a.id)
        descendant_ids = {loc.id for loc in descendants}
        assert descendant_ids == {b.id, c.id, d.id}

    def test_list_all_q_filter(self, db_session: Session) -> None:
        """list_all(q=...) is a case-insensitive substring match."""
        from app.repositories.location import LocationRepository

        repo = LocationRepository(db_session)
        repo.create(name="Garage")
        repo.create(name="Kitchen")
        db_session.commit()

        results = repo.list_all(q="garage")
        assert len(results) == 1
        assert results[0].name == "Garage"

        results_upper = repo.list_all(q="KITCHEN")
        assert len(results_upper) == 1
        assert results_upper[0].name == "Kitchen"

    def test_update_set_parent_id(self, db_session: Session) -> None:
        """update(set_parent_id=True, parent_id=X) changes the parent."""
        from app.repositories.location import LocationRepository

        repo = LocationRepository(db_session)
        home = repo.create(name="Home")
        work = repo.create(name="Work")
        db_session.flush()
        garage = repo.create(name="Garage", parent_id=home.id)
        db_session.commit()

        repo.update(garage, set_parent_id=True, parent_id=work.id)
        db_session.commit()

        refreshed = repo.get(garage.id)
        assert refreshed is not None
        assert refreshed.parent_id == work.id

    def test_update_set_parent_id_to_none(self, db_session: Session) -> None:
        """update(set_parent_id=True, parent_id=None) reparents to root."""
        from app.repositories.location import LocationRepository

        repo = LocationRepository(db_session)
        home = repo.create(name="Home")
        db_session.flush()
        garage = repo.create(name="Garage", parent_id=home.id)
        db_session.commit()

        repo.update(garage, set_parent_id=True, parent_id=None)
        db_session.commit()

        refreshed = repo.get(garage.id)
        assert refreshed is not None
        assert refreshed.parent_id is None


# ---------------------------------------------------------------------------
# 9. Service layer unit tests
# ---------------------------------------------------------------------------


class TestLocationService:
    """LocationService unit tests (no HTTP, tests business logic directly)."""

    def test_create_validates_parent_exists(self, db_session: Session) -> None:
        """Service.create with non-existent parent raises AppError 404."""
        from app.core.errors import AppError, ErrorCode
        from app.schemas.location import LocationCreate
        from app.services.location import LocationService

        svc = LocationService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.create(LocationCreate(name="Child", parent_id=9999))
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == ErrorCode.LOCATION_PARENT_NOT_FOUND

    def test_get_tree_build_correctness(self, db_session: Session) -> None:
        """get_tree() builds a correctly nested tree."""
        from app.repositories.location import LocationRepository
        from app.services.location import LocationService

        repo = LocationRepository(db_session)
        home = repo.create(name="Home")
        db_session.flush()
        garage = repo.create(name="Garage", parent_id=home.id)
        db_session.flush()
        repo.create(name="Toolbox", parent_id=garage.id)
        kitchen = repo.create(name="Kitchen", parent_id=home.id)  # noqa: F841
        db_session.commit()

        svc = LocationService(db_session)
        tree = svc.get_tree()

        # One root: Home.
        assert len(tree) == 1
        home_node = tree[0]
        assert home_node.name == "Home"
        assert len(home_node.children) == 2  # Garage + Kitchen

        # Find Garage child.
        garage_node = next(n for n in home_node.children if n.name == "Garage")
        assert len(garage_node.children) == 1
        assert garage_node.children[0].name == "Toolbox"

    def test_update_without_parent_id_does_not_reparent(self, db_session: Session) -> None:
        """PATCH without parent_id in payload does not clear the parent."""
        from app.repositories.location import LocationRepository
        from app.schemas.location import LocationUpdate
        from app.services.location import LocationService

        repo = LocationRepository(db_session)
        home = repo.create(name="Home")
        db_session.flush()
        garage = repo.create(name="Old Name", parent_id=home.id)
        db_session.commit()

        svc = LocationService(db_session)
        updated = svc.update(garage.id, LocationUpdate(name="New Name"))
        assert updated.parent_id == home.id  # unchanged
        assert updated.name == "New Name"
