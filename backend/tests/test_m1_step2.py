"""M1 Step 2 tests: Category self-referential tree.

Required coverage (easy-to-get-wrong logic, per M1.md §5 / §9 Step 2):
- Cycle rejected: reparent a node under itself (409).
- Cycle rejected: reparent a node under one of its descendants (409).
- Valid reparent succeeds (200).
- Delete-guard: deleting a non-empty node returns 409.
- Deleting a leaf node returns 204.
- /categories/tree DTO shape correct (nested children, no orphans).
- q= case-insensitive substring search on name.
- parent_id filter returns only children of that node.
- Migration 0005 upgrade clean on an empty DB.
- Migration 0005 downgrade clean (back to 0004).
- Step-1 location tests not broken: LocationService still passes its cycle
  and delete-guard checks via the shared TreeServiceMixin.

Also tests:
- Basic CRUD via HTTP (create, get, update, delete).
- 404 for missing category.
- Service-layer cycle checks (unit tested directly, not just HTTP).
- TreeServiceMixin used by both Location and Category (shared code path verified).
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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m1step2_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()  # Start empty.
    return f"sqlite:///{path_str}", path


def _make_fresh_session() -> Session:
    """In-memory SQLite session with all current models registered."""
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
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-m1-step2")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def test_client(temp_db: Path) -> Generator[TestClient]:  # noqa: ARG001
    """TestClient with a temp-file SQLite, full schema, and an authenticated session.

    Explicitly reloads every model module so that all SQLAlchemy models are
    re-registered on whatever ``app.db.base.Base`` instance is current at this
    point in the test session.  This guards against the cross-module ordering
    issue where a previous test's ``importlib.reload(db_base_mod)`` (inside
    ``_make_fresh_session``) created a new Base instance that no models are
    yet registered on.
    """
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
# Helper: create a category via HTTP
# ---------------------------------------------------------------------------


def _create_category(
    client: TestClient,
    name: str,
    parent_id: int | None = None,
    description: str | None = None,
) -> dict:  # type: ignore[type-arg]
    """POST /api/categories and return the response JSON dict."""
    payload: dict = {"name": name}  # type: ignore[type-arg]
    if parent_id is not None:
        payload["parent_id"] = parent_id
    if description is not None:
        payload["description"] = description

    resp = client.post("/api/categories", json=payload)
    assert resp.status_code == 201, f"create_category failed: {resp.status_code} {resp.json()}"
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. Basic CRUD
# ---------------------------------------------------------------------------


class TestCategoryCRUD:
    """Basic CRUD operations."""

    def test_create_root_category(self, test_client: TestClient) -> None:
        """POST /categories creates a root-level category (parent_id=null)."""
        data = _create_category(test_client, "Tools")
        assert data["name"] == "Tools"
        assert data["parent_id"] is None
        assert "id" in data
        assert "created_at" in data

    def test_create_child_category(self, test_client: TestClient) -> None:
        """POST /categories with parent_id creates a child category."""
        tools = _create_category(test_client, "Tools")
        power = _create_category(test_client, "Power tools", parent_id=tools["id"])
        assert power["parent_id"] == tools["id"]

    def test_get_category_by_id(self, test_client: TestClient) -> None:
        """GET /categories/{id} returns the category."""
        tools = _create_category(test_client, "Tools")
        resp = test_client.get(f"/api/categories/{tools['id']}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Tools"

    def test_get_category_404(self, test_client: TestClient) -> None:
        """GET /categories/{id} returns 404 for a non-existent id."""
        resp = test_client.get("/api/categories/9999")
        assert resp.status_code == 404

    def test_list_categories_empty(self, test_client: TestClient) -> None:
        """GET /categories returns an empty list when no categories exist."""
        resp = test_client.get("/api/categories")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_categories_returns_all(self, test_client: TestClient) -> None:
        """GET /categories returns all categories (flat)."""
        _create_category(test_client, "Electronics")
        _create_category(test_client, "Tools")
        resp = test_client.get("/api/categories")
        assert resp.status_code == 200
        names = {cat["name"] for cat in resp.json()}
        assert names == {"Electronics", "Tools"}

    def test_update_name(self, test_client: TestClient) -> None:
        """PATCH /categories/{id} can update the name."""
        tools = _create_category(test_client, "Old Name")
        resp = test_client.patch(f"/api/categories/{tools['id']}", json={"name": "New Name"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"

    def test_update_description(self, test_client: TestClient) -> None:
        """PATCH /categories/{id} can update the description."""
        tools = _create_category(test_client, "Tools")
        resp = test_client.patch(
            f"/api/categories/{tools['id']}", json={"description": "Hand and power tools"}
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "Hand and power tools"

    def test_delete_leaf_category(self, test_client: TestClient) -> None:
        """DELETE /categories/{id} on a leaf returns 204."""
        tools = _create_category(test_client, "Tools")
        resp = test_client.delete(f"/api/categories/{tools['id']}")
        assert resp.status_code == 204

        get_resp = test_client.get(f"/api/categories/{tools['id']}")
        assert get_resp.status_code == 404

    def test_delete_404_for_nonexistent(self, test_client: TestClient) -> None:
        """DELETE /categories/{id} returns 404 for a non-existent category."""
        resp = test_client.delete("/api/categories/9999")
        assert resp.status_code == 404

    def test_create_requires_auth(self, temp_db: Path) -> None:  # noqa: ARG002
        """POST /categories without a session returns 401."""
        from app.db.base import Base, get_engine
        from app.main import create_app

        engine = get_engine()
        Base.metadata.create_all(engine)
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post("/api/categories", json={"name": "No Auth"})
            assert resp.status_code == 401
        drop_all_sqlite(Base, engine)


# ---------------------------------------------------------------------------
# 2. Cycle prevention (easy-to-get-wrong)
# ---------------------------------------------------------------------------


class TestCategoryTreeCyclePrevention:
    """Cycle prevention — via shared TreeServiceMixin, same as Location."""

    def test_reparent_under_self_is_rejected(self, test_client: TestClient) -> None:
        """PATCH /categories/{id} with parent_id == id returns 409."""
        node = _create_category(test_client, "A")
        resp = test_client.patch(f"/api/categories/{node['id']}", json={"parent_id": node["id"]})
        assert resp.status_code == 409
        assert resp.json()["code"] == "tree.cycle"

    def test_reparent_under_direct_child_is_rejected(self, test_client: TestClient) -> None:
        """Parent → Child: reparenting Parent under Child is rejected (cycle)."""
        parent = _create_category(test_client, "Parent")
        child = _create_category(test_client, "Child", parent_id=parent["id"])

        resp = test_client.patch(f"/api/categories/{parent['id']}", json={"parent_id": child["id"]})
        assert resp.status_code == 409
        assert resp.json()["code"] == "tree.cycle"

    def test_reparent_under_distant_descendant_is_rejected(self, test_client: TestClient) -> None:
        """A → B → C: reparenting A under C (deep descendant) is rejected."""
        a = _create_category(test_client, "A")
        b = _create_category(test_client, "B", parent_id=a["id"])
        c = _create_category(test_client, "C", parent_id=b["id"])

        resp = test_client.patch(f"/api/categories/{a['id']}", json={"parent_id": c["id"]})
        assert resp.status_code == 409
        assert resp.json()["code"] == "tree.cycle"

    def test_valid_reparent_succeeds(self, test_client: TestClient) -> None:
        """Reparenting a node to a valid (non-descendant) node succeeds."""
        electronics = _create_category(test_client, "Electronics")
        tools = _create_category(test_client, "Tools")
        power = _create_category(test_client, "Power tools", parent_id=electronics["id"])

        # Reparent Power tools from Electronics to Tools — valid.
        resp = test_client.patch(f"/api/categories/{power['id']}", json={"parent_id": tools["id"]})
        assert resp.status_code == 200
        assert resp.json()["parent_id"] == tools["id"]

    def test_reparent_to_root_succeeds(self, test_client: TestClient) -> None:
        """Reparenting a node to null (root) succeeds."""
        tools = _create_category(test_client, "Tools")
        power = _create_category(test_client, "Power tools", parent_id=tools["id"])

        resp = test_client.patch(f"/api/categories/{power['id']}", json={"parent_id": None})
        assert resp.status_code == 200
        assert resp.json()["parent_id"] is None

    def test_service_cycle_check_self(self, db_session: Session) -> None:
        """CategoryService._assert_no_cycle raises AppError 409 on self-reference (unit test)."""
        from app.core.errors import AppError, ErrorCode
        from app.repositories.category import CategoryRepository
        from app.services.category import CategoryService

        repo = CategoryRepository(db_session)
        cat = repo.create(name="A")
        db_session.commit()

        svc = CategoryService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc._assert_no_cycle(cat.id, cat.id)
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == ErrorCode.TREE_CYCLE

    def test_service_cycle_check_descendant(self, db_session: Session) -> None:
        """CategoryService._assert_no_cycle raises AppError 409 for a descendant parent."""
        from app.core.errors import AppError, ErrorCode
        from app.repositories.category import CategoryRepository
        from app.services.category import CategoryService

        repo = CategoryRepository(db_session)
        a = repo.create(name="A")
        db_session.flush()
        b = repo.create(name="B", parent_id=a.id)
        db_session.flush()
        c = repo.create(name="C", parent_id=b.id)
        db_session.commit()

        svc = CategoryService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc._assert_no_cycle(a.id, c.id)
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == ErrorCode.TREE_CYCLE


# ---------------------------------------------------------------------------
# 3. Delete guard (easy-to-get-wrong)
# ---------------------------------------------------------------------------


class TestCategoryDeleteGuard:
    """Delete-guard — non-empty node must return 409."""

    def test_delete_non_empty_node_returns_409(self, test_client: TestClient) -> None:
        """DELETE /categories/{id} on a node with children returns 409."""
        parent = _create_category(test_client, "Tools")
        _create_category(test_client, "Power tools", parent_id=parent["id"])

        resp = test_client.delete(f"/api/categories/{parent['id']}")
        assert resp.status_code == 409
        assert resp.json()["code"] == "tree.delete_has_children"

    def test_delete_becomes_allowed_after_child_removed(self, test_client: TestClient) -> None:
        """After the child is deleted, the parent can be deleted too."""
        parent = _create_category(test_client, "Tools")
        child = _create_category(test_client, "Power tools", parent_id=parent["id"])

        # Delete child first.
        resp_child = test_client.delete(f"/api/categories/{child['id']}")
        assert resp_child.status_code == 204

        # Now parent should be deletable.
        resp_parent = test_client.delete(f"/api/categories/{parent['id']}")
        assert resp_parent.status_code == 204

    def test_service_delete_guard_unit(self, db_session: Session) -> None:
        """CategoryService.delete raises AppError 409 (via _assert_deletable) for a non-empty node."""
        from app.core.errors import AppError, ErrorCode
        from app.repositories.category import CategoryRepository
        from app.services.category import CategoryService

        repo = CategoryRepository(db_session)
        parent = repo.create(name="Parent")
        db_session.flush()
        repo.create(name="Child", parent_id=parent.id)
        db_session.commit()

        svc = CategoryService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.delete(parent.id)
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == ErrorCode.TREE_DELETE_HAS_CHILDREN


# ---------------------------------------------------------------------------
# 4. /categories/tree DTO shape
# ---------------------------------------------------------------------------


class TestCategoryTreeShape:
    """GET /categories/tree — DTO shape and nesting correctness."""

    def test_tree_empty(self, test_client: TestClient) -> None:
        """GET /categories/tree returns [] when no categories exist."""
        resp = test_client.get("/api/categories/tree")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_tree_flat_roots(self, test_client: TestClient) -> None:
        """All root categories appear as top-level nodes (no children)."""
        _create_category(test_client, "Electronics")
        _create_category(test_client, "Tools")
        resp = test_client.get("/api/categories/tree")
        assert resp.status_code == 200
        tree = resp.json()
        assert len(tree) == 2
        names = {n["name"] for n in tree}
        assert names == {"Electronics", "Tools"}
        for node in tree:
            assert node["children"] == []

    def test_tree_nested_shape(self, test_client: TestClient) -> None:
        """Tools → Power tools → Drills is correctly nested in the tree."""
        tools = _create_category(test_client, "Tools")
        power = _create_category(test_client, "Power tools", parent_id=tools["id"])
        drills = _create_category(test_client, "Drills", parent_id=power["id"])

        resp = test_client.get("/api/categories/tree")
        assert resp.status_code == 200
        tree = resp.json()

        # Exactly one root.
        assert len(tree) == 1
        root = tree[0]
        assert root["name"] == "Tools"
        assert root["id"] == tools["id"]

        # One child: Power tools.
        assert len(root["children"]) == 1
        power_node = root["children"][0]
        assert power_node["name"] == "Power tools"
        assert power_node["id"] == power["id"]

        # One child of Power tools: Drills.
        assert len(power_node["children"]) == 1
        drills_node = power_node["children"][0]
        assert drills_node["name"] == "Drills"
        assert drills_node["id"] == drills["id"]
        assert drills_node["children"] == []

    def test_tree_node_has_required_fields(self, test_client: TestClient) -> None:
        """Each tree node has id, name, description, parent_id, created_at, children."""
        _create_category(test_client, "Root", description="A root category")
        tree = test_client.get("/api/categories/tree").json()
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


class TestCategorySearch:
    """q= case-insensitive substring search."""

    def test_search_case_insensitive(self, test_client: TestClient) -> None:
        """q=tools matches 'Tools' regardless of case."""
        _create_category(test_client, "Tools")
        _create_category(test_client, "Electronics")

        for q in ["tools", "TOOLS", "Tools", "ools"]:
            resp = test_client.get(f"/api/categories?q={q}")
            assert resp.status_code == 200
            results = resp.json()
            assert len(results) == 1, f"Expected 1 result for q={q!r}, got {results}"
            assert results[0]["name"] == "Tools"

    def test_search_no_match(self, test_client: TestClient) -> None:
        """q= with no matching categories returns []."""
        _create_category(test_client, "Tools")
        resp = test_client.get("/api/categories?q=xyz_no_match")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_search_matches_multiple(self, test_client: TestClient) -> None:
        """Substring match can return multiple results."""
        _create_category(test_client, "Power tools")
        _create_category(test_client, "Hand tools")
        _create_category(test_client, "Electronics")

        resp = test_client.get("/api/categories?q=tools")
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 2
        names = {r["name"] for r in results}
        assert names == {"Power tools", "Hand tools"}

    def test_search_empty_q_returns_all(self, test_client: TestClient) -> None:
        """No q param returns all categories."""
        _create_category(test_client, "A")
        _create_category(test_client, "B")
        resp = test_client.get("/api/categories")
        assert resp.status_code == 200
        assert len(resp.json()) == 2


# ---------------------------------------------------------------------------
# 6. parent_id filter
# ---------------------------------------------------------------------------


class TestCategoryParentIdFilter:
    """parent_id= filter returns only children of that node."""

    def test_filter_by_parent_id(self, test_client: TestClient) -> None:
        """GET /categories?parent_id=X returns only direct children of X."""
        tools = _create_category(test_client, "Tools")
        electronics = _create_category(test_client, "Electronics")
        _create_category(test_client, "Power tools", parent_id=tools["id"])
        _create_category(test_client, "Hand tools", parent_id=tools["id"])
        _create_category(test_client, "Cameras", parent_id=electronics["id"])

        resp = test_client.get(f"/api/categories?parent_id={tools['id']}")
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 2
        names = {r["name"] for r in results}
        assert names == {"Power tools", "Hand tools"}

    def test_filter_by_parent_id_no_children(self, test_client: TestClient) -> None:
        """GET /categories?parent_id=X returns [] when X has no children."""
        leaf = _create_category(test_client, "Leaf")
        resp = test_client.get(f"/api/categories?parent_id={leaf['id']}")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# 7. Alembic migration 0005
# ---------------------------------------------------------------------------


class TestAlembicMigration0005:
    """Migration 0005 must apply cleanly on top of 0004 and downgrade cleanly."""

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

    def test_upgrade_head_creates_categories_table(self) -> None:
        """alembic upgrade head creates the categories table."""
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
                assert "categories" in table_names, (
                    f"categories table missing; found: {table_names}"
                )
                assert "locations" in table_names, "locations table must also exist at head"
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_upgrade_0005_clean(self) -> None:
        """Stepwise upgrade 0001→0002→0003→0004→0005 is clean."""
        url, db_path = _make_temp_db_url()
        try:
            for rev in ["0001", "0002", "0003", "0004", "0005"]:
                rc, out = self._run_alembic("upgrade", rev, url=url)
                assert rc == 0, f"alembic upgrade {rev} failed:\n{out}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "categories" in table_names
                assert "locations" in table_names
        finally:
            if db_path.exists():
                db_path.unlink()

    def test_downgrade_0005_drops_categories(self) -> None:
        """Downgrading from 0005 to 0004 drops categories, keeps locations."""
        url, db_path = _make_temp_db_url()
        try:
            rc_up, out_up = self._run_alembic("upgrade", "head", url=url)
            assert rc_up == 0, f"alembic upgrade failed:\n{out_up}"

            rc_down, out_down = self._run_alembic("downgrade", "0004", url=url)
            assert rc_down == 0, f"alembic downgrade to 0004 failed:\n{out_down}"

            engine = create_engine(url)
            with engine.connect() as conn:
                tables = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
                table_names = {row[0] for row in tables}
                assert "categories" not in table_names, (
                    "categories must be dropped after downgrade to 0004"
                )
                assert "locations" in table_names, "locations must still exist at 0004"
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


class TestCategoryRepository:
    """CategoryRepository unit tests (directly, no HTTP)."""

    def test_create_and_get(self, db_session: Session) -> None:
        """create() and get() roundtrip."""
        from app.repositories.category import CategoryRepository

        repo = CategoryRepository(db_session)
        cat = repo.create(name="Tools", description="All tools")
        db_session.commit()

        found = repo.get(cat.id)
        assert found is not None
        assert found.name == "Tools"
        assert found.description == "All tools"

    def test_get_returns_none_for_missing(self, db_session: Session) -> None:
        """get() returns None for a non-existent id."""
        from app.repositories.category import CategoryRepository

        repo = CategoryRepository(db_session)
        assert repo.get(9999) is None

    def test_has_children_false_for_leaf(self, db_session: Session) -> None:
        """has_children() is False for a leaf node."""
        from app.repositories.category import CategoryRepository

        repo = CategoryRepository(db_session)
        cat = repo.create(name="Leaf")
        db_session.commit()
        assert repo.has_children(cat.id) is False

    def test_has_children_true_for_parent(self, db_session: Session) -> None:
        """has_children() is True when the node has a child."""
        from app.repositories.category import CategoryRepository

        repo = CategoryRepository(db_session)
        parent = repo.create(name="Parent")
        db_session.flush()
        repo.create(name="Child", parent_id=parent.id)
        db_session.commit()
        assert repo.has_children(parent.id) is True

    def test_get_descendants_empty_for_leaf(self, db_session: Session) -> None:
        """get_descendants() returns [] for a leaf node."""
        from app.repositories.category import CategoryRepository

        repo = CategoryRepository(db_session)
        leaf = repo.create(name="Leaf")
        db_session.commit()
        assert repo.get_descendants(leaf.id) == []

    def test_get_descendants_multi_level(self, db_session: Session) -> None:
        """get_descendants() returns all descendants across multiple levels."""
        from app.repositories.category import CategoryRepository

        repo = CategoryRepository(db_session)
        a = repo.create(name="A")
        db_session.flush()
        b = repo.create(name="B", parent_id=a.id)
        db_session.flush()
        c = repo.create(name="C", parent_id=b.id)
        db_session.flush()
        d = repo.create(name="D", parent_id=a.id)  # second child of A
        db_session.commit()

        descendants = repo.get_descendants(a.id)
        descendant_ids = {cat.id for cat in descendants}
        assert descendant_ids == {b.id, c.id, d.id}

    def test_list_all_q_filter(self, db_session: Session) -> None:
        """list_all(q=...) is a case-insensitive substring match."""
        from app.repositories.category import CategoryRepository

        repo = CategoryRepository(db_session)
        repo.create(name="Tools")
        repo.create(name="Electronics")
        db_session.commit()

        results = repo.list_all(q="tools")
        assert len(results) == 1
        assert results[0].name == "Tools"

        results_upper = repo.list_all(q="ELECTRONICS")
        assert len(results_upper) == 1
        assert results_upper[0].name == "Electronics"

    def test_update_set_parent_id(self, db_session: Session) -> None:
        """update(set_parent_id=True, parent_id=X) changes the parent."""
        from app.repositories.category import CategoryRepository

        repo = CategoryRepository(db_session)
        tools = repo.create(name="Tools")
        electronics = repo.create(name="Electronics")
        db_session.flush()
        power = repo.create(name="Power tools", parent_id=tools.id)
        db_session.commit()

        repo.update(power, set_parent_id=True, parent_id=electronics.id)
        db_session.commit()

        refreshed = repo.get(power.id)
        assert refreshed is not None
        assert refreshed.parent_id == electronics.id

    def test_update_set_parent_id_to_none(self, db_session: Session) -> None:
        """update(set_parent_id=True, parent_id=None) reparents to root."""
        from app.repositories.category import CategoryRepository

        repo = CategoryRepository(db_session)
        tools = repo.create(name="Tools")
        db_session.flush()
        power = repo.create(name="Power tools", parent_id=tools.id)
        db_session.commit()

        repo.update(power, set_parent_id=True, parent_id=None)
        db_session.commit()

        refreshed = repo.get(power.id)
        assert refreshed is not None
        assert refreshed.parent_id is None


# ---------------------------------------------------------------------------
# 9. Service layer unit tests
# ---------------------------------------------------------------------------


class TestCategoryService:
    """CategoryService unit tests (no HTTP, tests business logic directly)."""

    def test_create_validates_parent_exists(self, db_session: Session) -> None:
        """Service.create with non-existent parent raises AppError 404."""
        from app.core.errors import AppError, ErrorCode
        from app.schemas.category import CategoryCreate
        from app.services.category import CategoryService

        svc = CategoryService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc.create(CategoryCreate(name="Child", parent_id=9999))
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == ErrorCode.CATEGORY_PARENT_NOT_FOUND

    def test_get_tree_build_correctness(self, db_session: Session) -> None:
        """get_tree() builds a correctly nested tree."""
        from app.repositories.category import CategoryRepository
        from app.services.category import CategoryService

        repo = CategoryRepository(db_session)
        tools = repo.create(name="Tools")
        db_session.flush()
        power = repo.create(name="Power tools", parent_id=tools.id)
        db_session.flush()
        repo.create(name="Drills", parent_id=power.id)
        repo.create(name="Hand tools", parent_id=tools.id)
        db_session.commit()

        svc = CategoryService(db_session)
        tree = svc.get_tree()

        # One root: Tools.
        assert len(tree) == 1
        tools_node = tree[0]
        assert tools_node.name == "Tools"
        assert len(tools_node.children) == 2  # Power tools + Hand tools

        # Find Power tools child.
        power_node = next(n for n in tools_node.children if n.name == "Power tools")
        assert len(power_node.children) == 1
        assert power_node.children[0].name == "Drills"

    def test_update_without_parent_id_does_not_reparent(self, db_session: Session) -> None:
        """PATCH without parent_id in payload does not clear the parent."""
        from app.repositories.category import CategoryRepository
        from app.schemas.category import CategoryUpdate
        from app.services.category import CategoryService

        repo = CategoryRepository(db_session)
        tools = repo.create(name="Tools")
        db_session.flush()
        power = repo.create(name="Old Name", parent_id=tools.id)
        db_session.commit()

        svc = CategoryService(db_session)
        updated = svc.update(power.id, CategoryUpdate(name="New Name"))
        assert updated.parent_id == tools.id  # unchanged
        assert updated.name == "New Name"


# ---------------------------------------------------------------------------
# 10. Shared TreeServiceMixin: verify both services use the same code path
# ---------------------------------------------------------------------------


class TestTreeServiceMixinShared:
    """Verify the shared mixin is genuinely reused (not copy-pasted)."""

    def test_location_service_uses_mixin(self) -> None:
        """LocationService should be a subclass of TreeServiceMixin."""
        from app.services.location import LocationService
        from app.services.tree import TreeServiceMixin

        assert issubclass(LocationService, TreeServiceMixin)

    def test_category_service_uses_mixin(self) -> None:
        """CategoryService should be a subclass of TreeServiceMixin."""
        from app.services.category import CategoryService
        from app.services.tree import TreeServiceMixin

        assert issubclass(CategoryService, TreeServiceMixin)

    def test_assert_no_cycle_is_inherited_from_mixin(self) -> None:
        """Both services inherit _assert_no_cycle from TreeServiceMixin (same function)."""
        from app.services.category import CategoryService
        from app.services.location import LocationService
        from app.services.tree import TreeServiceMixin

        # Both resolve _assert_no_cycle to the same method on the mixin.
        assert CategoryService._assert_no_cycle is TreeServiceMixin._assert_no_cycle
        assert LocationService._assert_no_cycle is TreeServiceMixin._assert_no_cycle

    def test_assert_deletable_is_inherited_from_mixin(self) -> None:
        """Both services inherit _assert_deletable from TreeServiceMixin (same function)."""
        from app.services.category import CategoryService
        from app.services.location import LocationService
        from app.services.tree import TreeServiceMixin

        assert CategoryService._assert_deletable is TreeServiceMixin._assert_deletable
        assert LocationService._assert_deletable is TreeServiceMixin._assert_deletable

    def test_location_cycle_guard_still_works_via_mixin(self, db_session: Session) -> None:
        """Location cycle guard still passes through the shared mixin after Step-2 refactor."""
        from app.core.errors import AppError, ErrorCode
        from app.repositories.location import LocationRepository
        from app.services.location import LocationService

        repo = LocationRepository(db_session)
        a = repo.create(name="A")
        db_session.flush()
        b = repo.create(name="B", parent_id=a.id)
        db_session.commit()

        svc = LocationService(db_session)
        with pytest.raises(AppError) as exc_info:
            svc._assert_no_cycle(a.id, b.id)
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == ErrorCode.TREE_CYCLE

    def test_location_delete_guard_still_works_via_mixin(self, db_session: Session) -> None:
        """Location delete guard still passes through the shared mixin after Step-2 refactor."""
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
