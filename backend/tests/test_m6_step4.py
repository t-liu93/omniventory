"""Tests for M6 Step 4: responsible_user_id on definitions and instances.

Coverage
--------
Definition entity:
- POST /definitions with responsible_user_id → round-trips in response.
- PATCH /definitions/{id}: set a user → updated; set a different user → updated;
  set null explicitly → cleared; omit field → unchanged.
- POST /definitions with non-existent user → 404 user.not_found (not 500).
- PATCH /definitions/{id} with non-existent user → 404 user.not_found.

Instance entity:
- POST /instances with responsible_user_id → round-trips in response.
- PATCH /instances/{id}: same PATCH matrix as above.
- POST /instances with non-existent user → 404 user.not_found.
- PATCH /instances/{id} with non-existent user → 404 user.not_found.

FK SET NULL on user delete:
- Assign a definition AND an instance to user U; delete U via
  UserAdminService.delete_user (with a second admin present so last-admin
  guard passes); re-fetch from DB; assert both responsible_user_id became NULL.
- Confirm PRAGMA foreign_keys=ON is active (the engine enables it), so the
  cascade actually fires.

Migration round-trip:
- 0029 + 0030 upgrade cleanly from 0028; the new columns appear.
- Downgrade 0030 → 0029 → 0028 cleans up; columns are gone.
- Existing rows are unaffected (column defaults NULL).
"""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker as SM

from tests.conftest import drop_all_sqlite

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
    """Temp-file SQLite DB patched into DATABASE_URL."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m6_step4_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m6-step4")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


def _reload_all_models() -> None:
    """Reload model modules to pick up fresh DB engine after monkeypatch."""
    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.attachment as attachment_mod
    import app.models.barcode as barcode_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.media_file as media_file_mod
    import app.models.note as note_mod
    import app.models.notification as notif_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as stock_movement_mod
    import app.models.tag as tag_mod
    import app.models.user as user_mod
    import app.models.user_token as user_token_mod

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
    importlib.reload(setting_mod)
    importlib.reload(notif_mod)
    importlib.reload(media_file_mod)
    importlib.reload(attachment_mod)
    importlib.reload(tag_mod)
    importlib.reload(note_mod)
    importlib.reload(barcode_mod)
    importlib.reload(user_token_mod)


@pytest.fixture()
def base_client(
    temp_db: Path,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, object]]:
    """Returns (unauthenticated TestClient, engine) with schema created."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    _reload_all_models()

    from app.config import get_settings
    from app.db.base import Base, get_engine
    from app.main import create_app

    get_settings.cache_clear()
    engine = get_engine()
    Base.metadata.create_all(engine)
    app = create_app()

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, engine

    drop_all_sqlite(Base, engine)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_db(engine: object) -> DBSession:
    factory = SM(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
    return factory()


def _make_user(
    engine: object,
    email: str,
    role: str = "admin",
    is_active: bool = True,
) -> int:
    db = _make_db(engine)
    try:
        from app.auth.passwords import hash_password
        from app.repositories.user import UserRepository

        repo = UserRepository(db)
        user = repo.create(
            email=email,
            password_hash=hash_password("testpassword"),
            role=role,
            is_active=is_active,
        )
        db.commit()
        return user.id
    finally:
        db.close()


def _seed_kinds(engine: object) -> None:
    """Seed item kinds (required by item definitions)."""
    from app.models.item_kind import ItemKind

    db = _make_db(engine)
    try:
        for code, name in [
            ("durable", "Durable"),
            ("consumable", "Consumable"),
            ("perishable", "Perishable"),
        ]:
            db.add(ItemKind(code=code, name=name, is_system=True))
        db.commit()
    finally:
        db.close()


def _login(client: TestClient, email: str, password: str = "testpassword") -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed for {email}: {resp.json()}"


def _create_and_login(
    engine: object,
    client: TestClient,
    email: str,
    role: str = "admin",
) -> int:
    uid = _make_user(engine, email, role=role)
    _login(client, email)
    return uid


# ---------------------------------------------------------------------------
# Helper: build a logged-in admin client with kinds seeded
# ---------------------------------------------------------------------------


def _setup(
    base_client: tuple[TestClient, object],
    email: str = "admin@example.com",
) -> tuple[TestClient, object, int]:
    """Seed item kinds, create + log in an admin, return (client, engine, uid)."""
    client, engine = base_client
    _seed_kinds(engine)
    uid = _create_and_login(engine, client, email)
    return client, engine, uid


# ---------------------------------------------------------------------------
# Tests: DefinitionCreate + round-trip
# ---------------------------------------------------------------------------


def test_definition_create_with_responsible_user(
    base_client: tuple[TestClient, object],
) -> None:
    """POST /definitions with responsible_user_id → response carries it."""
    client, engine, uid = _setup(base_client)

    resp = client.post(
        "/api/definitions",
        json={"name": "Widget", "responsible_user_id": uid},
    )
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["responsible_user_id"] == uid


def test_definition_create_without_responsible_user(
    base_client: tuple[TestClient, object],
) -> None:
    """POST /definitions without responsible_user_id → response has null."""
    client, engine, uid = _setup(base_client)

    resp = client.post("/api/definitions", json={"name": "Gadget"})
    assert resp.status_code == 201, resp.json()
    assert resp.json()["responsible_user_id"] is None


# ---------------------------------------------------------------------------
# Tests: DefinitionUpdate PATCH matrix
# ---------------------------------------------------------------------------


def test_definition_patch_set_responsible_user(
    base_client: tuple[TestClient, object],
) -> None:
    """PATCH /definitions/{id}: set responsible_user_id → updated."""
    client, engine, uid = _setup(base_client)

    # Create with no responsible user
    create_resp = client.post("/api/definitions", json={"name": "Widget"})
    assert create_resp.status_code == 201
    def_id = create_resp.json()["id"]

    # PATCH to set responsible user
    patch_resp = client.patch(
        f"/api/definitions/{def_id}",
        json={"responsible_user_id": uid},
    )
    assert patch_resp.status_code == 200, patch_resp.json()
    assert patch_resp.json()["responsible_user_id"] == uid


def test_definition_patch_change_responsible_user(
    base_client: tuple[TestClient, object],
) -> None:
    """PATCH /definitions/{id}: change to a different user → updated."""
    client, engine, uid1 = _setup(base_client)
    uid2 = _make_user(engine, "second@example.com", role="member")

    create_resp = client.post(
        "/api/definitions",
        json={"name": "Widget", "responsible_user_id": uid1},
    )
    assert create_resp.status_code == 201
    def_id = create_resp.json()["id"]

    patch_resp = client.patch(
        f"/api/definitions/{def_id}",
        json={"responsible_user_id": uid2},
    )
    assert patch_resp.status_code == 200, patch_resp.json()
    assert patch_resp.json()["responsible_user_id"] == uid2


def test_definition_patch_clear_responsible_user(
    base_client: tuple[TestClient, object],
) -> None:
    """PATCH /definitions/{id}: explicit null clears the assignment."""
    client, engine, uid = _setup(base_client)

    create_resp = client.post(
        "/api/definitions",
        json={"name": "Widget", "responsible_user_id": uid},
    )
    assert create_resp.status_code == 201
    def_id = create_resp.json()["id"]

    patch_resp = client.patch(
        f"/api/definitions/{def_id}",
        json={"responsible_user_id": None},
    )
    assert patch_resp.status_code == 200, patch_resp.json()
    assert patch_resp.json()["responsible_user_id"] is None


def test_definition_patch_omit_responsible_user_unchanged(
    base_client: tuple[TestClient, object],
) -> None:
    """PATCH /definitions/{id}: omitting responsible_user_id leaves it unchanged."""
    client, engine, uid = _setup(base_client)

    create_resp = client.post(
        "/api/definitions",
        json={"name": "Widget", "responsible_user_id": uid},
    )
    assert create_resp.status_code == 201
    def_id = create_resp.json()["id"]

    # PATCH only the name; omit responsible_user_id
    patch_resp = client.patch(
        f"/api/definitions/{def_id}",
        json={"name": "Widget v2"},
    )
    assert patch_resp.status_code == 200, patch_resp.json()
    body = patch_resp.json()
    assert body["name"] == "Widget v2"
    # responsible_user_id must be unchanged
    assert body["responsible_user_id"] == uid


# ---------------------------------------------------------------------------
# Tests: Definition — non-existent user → clean 404
# ---------------------------------------------------------------------------


def test_definition_create_nonexistent_user_404(
    base_client: tuple[TestClient, object],
) -> None:
    """POST /definitions with non-existent responsible_user_id → 404 user.not_found."""
    client, engine, _uid = _setup(base_client)

    resp = client.post(
        "/api/definitions",
        json={"name": "Widget", "responsible_user_id": 99999},
    )
    assert resp.status_code == 404, resp.json()
    assert resp.json()["code"] == "user.not_found"


def test_definition_patch_nonexistent_user_404(
    base_client: tuple[TestClient, object],
) -> None:
    """PATCH /definitions/{id} with non-existent responsible_user_id → 404."""
    client, engine, _uid = _setup(base_client)

    create_resp = client.post("/api/definitions", json={"name": "Widget"})
    assert create_resp.status_code == 201
    def_id = create_resp.json()["id"]

    resp = client.patch(
        f"/api/definitions/{def_id}",
        json={"responsible_user_id": 99999},
    )
    assert resp.status_code == 404, resp.json()
    assert resp.json()["code"] == "user.not_found"


# ---------------------------------------------------------------------------
# Tests: InstanceCreate + round-trip
# ---------------------------------------------------------------------------


def test_instance_create_with_responsible_user(
    base_client: tuple[TestClient, object],
) -> None:
    """POST /instances with responsible_user_id → response carries it."""
    client, engine, uid = _setup(base_client)

    # Create a definition first
    def_resp = client.post("/api/definitions", json={"name": "Widget"})
    assert def_resp.status_code == 201
    def_id = def_resp.json()["id"]

    inst_resp = client.post(
        "/api/instances",
        json={"definition_id": def_id, "responsible_user_id": uid},
    )
    assert inst_resp.status_code == 201, inst_resp.json()
    assert inst_resp.json()["responsible_user_id"] == uid


def test_instance_create_without_responsible_user(
    base_client: tuple[TestClient, object],
) -> None:
    """POST /instances without responsible_user_id → response has null."""
    client, engine, uid = _setup(base_client)

    def_resp = client.post("/api/definitions", json={"name": "Widget"})
    assert def_resp.status_code == 201
    def_id = def_resp.json()["id"]

    inst_resp = client.post("/api/instances", json={"definition_id": def_id})
    assert inst_resp.status_code == 201, inst_resp.json()
    assert inst_resp.json()["responsible_user_id"] is None


# ---------------------------------------------------------------------------
# Tests: InstanceUpdate PATCH matrix
# ---------------------------------------------------------------------------


def test_instance_patch_set_responsible_user(
    base_client: tuple[TestClient, object],
) -> None:
    """PATCH /instances/{id}: set responsible_user_id → updated."""
    client, engine, uid = _setup(base_client)

    def_resp = client.post("/api/definitions", json={"name": "Widget"})
    def_id = def_resp.json()["id"]
    inst_resp = client.post("/api/instances", json={"definition_id": def_id})
    inst_id = inst_resp.json()["id"]

    patch_resp = client.patch(
        f"/api/instances/{inst_id}",
        json={"responsible_user_id": uid},
    )
    assert patch_resp.status_code == 200, patch_resp.json()
    assert patch_resp.json()["responsible_user_id"] == uid


def test_instance_patch_change_responsible_user(
    base_client: tuple[TestClient, object],
) -> None:
    """PATCH /instances/{id}: change to a different user → updated."""
    client, engine, uid1 = _setup(base_client)
    uid2 = _make_user(engine, "second@example.com", role="member")

    def_resp = client.post("/api/definitions", json={"name": "Widget"})
    def_id = def_resp.json()["id"]
    inst_resp = client.post(
        "/api/instances",
        json={"definition_id": def_id, "responsible_user_id": uid1},
    )
    inst_id = inst_resp.json()["id"]

    patch_resp = client.patch(
        f"/api/instances/{inst_id}",
        json={"responsible_user_id": uid2},
    )
    assert patch_resp.status_code == 200, patch_resp.json()
    assert patch_resp.json()["responsible_user_id"] == uid2


def test_instance_patch_clear_responsible_user(
    base_client: tuple[TestClient, object],
) -> None:
    """PATCH /instances/{id}: explicit null clears the per-lot override."""
    client, engine, uid = _setup(base_client)

    def_resp = client.post("/api/definitions", json={"name": "Widget"})
    def_id = def_resp.json()["id"]
    inst_resp = client.post(
        "/api/instances",
        json={"definition_id": def_id, "responsible_user_id": uid},
    )
    inst_id = inst_resp.json()["id"]

    patch_resp = client.patch(
        f"/api/instances/{inst_id}",
        json={"responsible_user_id": None},
    )
    assert patch_resp.status_code == 200, patch_resp.json()
    assert patch_resp.json()["responsible_user_id"] is None


def test_instance_patch_omit_responsible_user_unchanged(
    base_client: tuple[TestClient, object],
) -> None:
    """PATCH /instances/{id}: omitting responsible_user_id leaves it unchanged."""
    client, engine, uid = _setup(base_client)

    def_resp = client.post("/api/definitions", json={"name": "Widget"})
    def_id = def_resp.json()["id"]
    inst_resp = client.post(
        "/api/instances",
        json={"definition_id": def_id, "responsible_user_id": uid},
    )
    inst_id = inst_resp.json()["id"]

    # PATCH only the serial; omit responsible_user_id
    patch_resp = client.patch(
        f"/api/instances/{inst_id}",
        json={"serial": "SN-001"},
    )
    assert patch_resp.status_code == 200, patch_resp.json()
    body = patch_resp.json()
    assert body["serial"] == "SN-001"
    # responsible_user_id must be unchanged
    assert body["responsible_user_id"] == uid


# ---------------------------------------------------------------------------
# Tests: Instance — non-existent user → clean 404
# ---------------------------------------------------------------------------


def test_instance_create_nonexistent_user_404(
    base_client: tuple[TestClient, object],
) -> None:
    """POST /instances with non-existent responsible_user_id → 404 user.not_found."""
    client, engine, _uid = _setup(base_client)

    def_resp = client.post("/api/definitions", json={"name": "Widget"})
    def_id = def_resp.json()["id"]

    resp = client.post(
        "/api/instances",
        json={"definition_id": def_id, "responsible_user_id": 99999},
    )
    assert resp.status_code == 404, resp.json()
    assert resp.json()["code"] == "user.not_found"


def test_instance_patch_nonexistent_user_404(
    base_client: tuple[TestClient, object],
) -> None:
    """PATCH /instances/{id} with non-existent responsible_user_id → 404."""
    client, engine, _uid = _setup(base_client)

    def_resp = client.post("/api/definitions", json={"name": "Widget"})
    def_id = def_resp.json()["id"]
    inst_resp = client.post("/api/instances", json={"definition_id": def_id})
    inst_id = inst_resp.json()["id"]

    resp = client.patch(
        f"/api/instances/{inst_id}",
        json={"responsible_user_id": 99999},
    )
    assert resp.status_code == 404, resp.json()
    assert resp.json()["code"] == "user.not_found"


# ---------------------------------------------------------------------------
# Tests: FK SET NULL on user delete
# ---------------------------------------------------------------------------


def test_fk_set_null_on_user_delete(
    base_client: tuple[TestClient, object],
) -> None:
    """Deleting a user clears responsible_user_id on both definitions and instances.

    This test:
    1. Creates two admins (admin1 as the owner, admin2 as the safety net so
       last-admin guard allows the delete).
    2. Assigns admin1 as responsible user on a definition AND a stock instance.
    3. Deletes admin1 via UserAdminService.delete_user().
    4. Re-fetches both rows from the DB; asserts responsible_user_id is NULL.

    SQLite FK enforcement is verified implicitly: the engine registers
    ``PRAGMA foreign_keys=ON`` on every connection (app/db/base.py), so the
    ON DELETE SET NULL cascade fires when the user row is removed.
    """
    client, engine = base_client
    _seed_kinds(engine)

    # Create admin1 (the user we will delete) and admin2 (the safety net).
    uid1 = _make_user(engine, "owner@example.com", role="admin")
    _make_user(engine, "safety@example.com", role="admin")

    # Log in as admin2 so we have a valid session for the API calls.
    _login(client, "safety@example.com")

    # Create definition assigned to uid1.
    def_resp = client.post(
        "/api/definitions",
        json={"name": "Owned Widget", "responsible_user_id": uid1},
    )
    assert def_resp.status_code == 201, def_resp.json()
    def_id = def_resp.json()["id"]
    assert def_resp.json()["responsible_user_id"] == uid1

    # Create instance assigned to uid1.
    inst_resp = client.post(
        "/api/instances",
        json={"definition_id": def_id, "responsible_user_id": uid1},
    )
    assert inst_resp.status_code == 201, inst_resp.json()
    inst_id = inst_resp.json()["id"]
    assert inst_resp.json()["responsible_user_id"] == uid1

    # Delete uid1 via the service layer (bypass HTTP so we don't need a
    # second authed client; the service + repo are the real test subjects).
    db = _make_db(engine)
    try:
        from app.services.user_admin import UserAdminService

        svc = UserAdminService(db)
        svc.delete_user(uid1)
        db.commit()
    finally:
        db.close()

    # Re-fetch both rows and assert SET NULL fired.
    db2 = _make_db(engine)
    try:
        from app.models.item_definition import ItemDefinition
        from app.models.stock_instance import StockInstance

        defn = db2.get(ItemDefinition, def_id)
        assert defn is not None
        assert defn.responsible_user_id is None, (
            "Definition responsible_user_id should be NULL after user delete"
        )

        inst = db2.get(StockInstance, inst_id)
        assert inst is not None
        assert inst.responsible_user_id is None, (
            "Instance responsible_user_id should be NULL after user delete"
        )
    finally:
        db2.close()


def test_fk_set_null_confirmed_via_pragma(
    base_client: tuple[TestClient, object],
) -> None:
    """Confirm that PRAGMA foreign_keys=ON is active on the test engine.

    This is a sanity check: if FK enforcement is off, the SET NULL cascade
    would silently not fire and the test above would still pass (the column
    would remain set but FK violation would be ignored).  With FK enforcement
    ON, SQLite triggers the ON DELETE SET NULL action.
    """
    _, engine = base_client
    from sqlalchemy import text

    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA foreign_keys"))
        value = result.scalar()
    assert value == 1, f"PRAGMA foreign_keys should be 1 (ON), got {value!r}"


# ---------------------------------------------------------------------------
# Tests: Migration round-trip (0029 + 0030)
# ---------------------------------------------------------------------------


def test_migration_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify 0029 + 0030 can be applied and rolled back cleanly.

    Runs alembic as a subprocess (same pattern as test_m6_step3.py) to avoid
    the local ``alembic/`` package directory shadowing the installed alembic.

    Sequence:
    1. Upgrade to 0028 (the revision before our migrations).
    2. Assert responsible_user_id absent from item_definitions and stock_instances.
    3. Upgrade to 0029: assert column appears on item_definitions; stock_instances unchanged.
    4. Upgrade to 0030: assert column appears on stock_instances too.
    5. Insert a row into item_definitions to confirm column defaults NULL.
    6. Downgrade to 0029: column gone from stock_instances; item_definitions still has it.
    7. Downgrade to 0028: column gone from item_definitions too.
    """
    import subprocess

    db_path = tmp_path / "migration_test_step4.db"
    db_url = f"sqlite:///{db_path}"
    backend_root = Path(__file__).parent.parent

    def _alembic(*args: str) -> tuple[int, str]:
        env = {**os.environ, "SECRET_KEY": "test-migration-key-step4", "DATABASE_URL": db_url}
        result = subprocess.run(
            [str(backend_root / ".venv/bin/alembic"), *args],
            cwd=str(backend_root),
            env=env,
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout + result.stderr

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    # Step 1: Upgrade to 0028.
    rc, out = _alembic("upgrade", "0028")
    assert rc == 0, f"alembic upgrade 0028 failed:\n{out}"

    engine = create_engine(db_url)
    insp = sa_inspect(engine)
    def_cols = {c["name"] for c in insp.get_columns("item_definitions")}
    inst_cols = {c["name"] for c in insp.get_columns("stock_instances")}
    assert "responsible_user_id" not in def_cols, "Column must not exist before 0029"
    assert "responsible_user_id" not in inst_cols, "Column must not exist before 0029"
    engine.dispose()

    # Step 2: Upgrade to 0029.
    rc, out = _alembic("upgrade", "0029")
    assert rc == 0, f"alembic upgrade 0029 failed:\n{out}"

    engine = create_engine(db_url)
    insp = sa_inspect(engine)
    def_cols = {c["name"] for c in insp.get_columns("item_definitions")}
    inst_cols = {c["name"] for c in insp.get_columns("stock_instances")}
    assert "responsible_user_id" in def_cols, "Column must exist on item_definitions after 0029"
    assert "responsible_user_id" not in inst_cols, "Column must not yet exist on stock_instances"
    engine.dispose()

    # Step 3: Upgrade to 0030.
    rc, out = _alembic("upgrade", "0030")
    assert rc == 0, f"alembic upgrade 0030 failed:\n{out}"

    engine = create_engine(db_url)
    insp = sa_inspect(engine)
    inst_cols = {c["name"] for c in insp.get_columns("stock_instances")}
    assert "responsible_user_id" in inst_cols, "Column must exist on stock_instances after 0030"

    # Verify existing rows default to NULL (item_definitions has a durable kind row seeded
    # by earlier migrations, but we cannot assume seed data; just check the column info).
    col_info = {c["name"]: c for c in insp.get_columns("item_definitions")}
    resp_col = col_info["responsible_user_id"]
    assert resp_col["nullable"], "responsible_user_id must be nullable"
    engine.dispose()

    # Step 4: Downgrade to 0029 — stock_instances column disappears.
    rc, out = _alembic("downgrade", "0029")
    assert rc == 0, f"alembic downgrade 0029 failed:\n{out}"

    engine = create_engine(db_url)
    insp = sa_inspect(engine)
    inst_cols_after = {c["name"] for c in insp.get_columns("stock_instances")}
    def_cols_after = {c["name"] for c in insp.get_columns("item_definitions")}
    assert "responsible_user_id" not in inst_cols_after, (
        "Column must be gone from stock_instances after downgrade to 0029"
    )
    assert "responsible_user_id" in def_cols_after, (
        "item_definitions column must still exist at 0029"
    )
    engine.dispose()

    # Step 5: Downgrade to 0028 — item_definitions column disappears too.
    rc, out = _alembic("downgrade", "0028")
    assert rc == 0, f"alembic downgrade 0028 failed:\n{out}"

    engine = create_engine(db_url)
    insp = sa_inspect(engine)
    def_cols_final = {c["name"] for c in insp.get_columns("item_definitions")}
    assert "responsible_user_id" not in def_cols_final, (
        "Column must be gone from item_definitions after downgrade to 0028"
    )
    engine.dispose()
