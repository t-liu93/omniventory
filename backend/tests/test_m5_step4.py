"""Tests for M5 Step 4: custom fields (JSON) on definitions and instances.

Coverage
--------
- Valid scalar map round-trips on a definition (create → response is a parsed
  dict; PATCH updates it; PATCH ``custom_fields=null`` clears it; omitting on
  PATCH leaves it unchanged).
- Same round-trips on a stock instance.
- Nested/object and array values are rejected (``validation.invalid_input``/422).
- Key cap (> 64 chars) → 422.
- String-value cap (> 1024 chars) → 422.
- Field-count cap (> 50 fields) → 422.
- NULL default (created without custom_fields → ``custom_fields=None``).
- Mixed scalar types (str/int/float/bool/null) preserved with correct types on
  round-trip.
- Persisted column stores a JSON string (inspected via direct DB query).
- Migration 0025 upgrade + downgrade on a DB at 0024; existing rows unaffected.
- Migration 0026 upgrade + downgrade on a DB at 0025 (via 0026's downgrade).
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Fixture infrastructure
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
    """Temp-file SQLite DB; patches DATABASE_URL so get_engine() uses it."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m5_step4_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m5-step4")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def test_client(
    temp_db: Path,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient]:
    """TestClient with full schema (all models including M5 Step 4 columns),
    authenticated admin, and isolated media dir."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

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
    import app.models.note as note_mod
    import app.models.notification as notif_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
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
    importlib.reload(setting_mod)
    importlib.reload(notif_mod)
    importlib.reload(media_file_mod)
    importlib.reload(attachment_mod)
    importlib.reload(tag_mod)
    importlib.reload(note_mod)

    from app.config import get_settings
    from app.db.base import Base, get_engine
    from app.main import create_app

    get_settings.cache_clear()
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

        resp = client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "adminpass"},
        )
        assert resp.status_code == 200
        yield client

    drop_all_sqlite(Base, engine)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def _create_definition(
    client: TestClient,
    name: str,
    *,
    custom_fields: dict | None = None,
    stock_tracking_mode: str = "none",
) -> dict:  # type: ignore[type-arg]
    payload: dict = {"name": name, "stock_tracking_mode": stock_tracking_mode}  # type: ignore[type-arg]
    if custom_fields is not None:
        payload["custom_fields"] = custom_fields
    resp = client.post("/api/definitions", json=payload)
    assert resp.status_code == 201, f"create_definition failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_instance(
    client: TestClient,
    definition_id: int,
    *,
    custom_fields: dict | None = None,  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    payload: dict = {"definition_id": definition_id}  # type: ignore[type-arg]
    if custom_fields is not None:
        payload["custom_fields"] = custom_fields
    resp = client.post("/api/instances", json=payload)
    assert resp.status_code == 201, f"create_instance failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _get_definition(client: TestClient, def_id: int) -> dict:  # type: ignore[type-arg]
    resp = client.get(f"/api/definitions/{def_id}")
    assert resp.status_code == 200
    return resp.json()  # type: ignore[return-value]


def _get_instance(client: TestClient, inst_id: int) -> dict:  # type: ignore[type-arg]
    resp = client.get(f"/api/instances/{inst_id}")
    assert resp.status_code == 200
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. Definition custom fields — happy path
# ---------------------------------------------------------------------------


class TestDefinitionCustomFields:
    """Custom fields round-trip tests for item definitions."""

    def test_create_with_custom_fields_returns_parsed_dict(self, test_client: TestClient) -> None:
        """POST with custom_fields → response has parsed dict, not raw JSON string."""
        fields = {"voltage": "5V", "slots": 4, "active": True, "notes": None}
        defn = _create_definition(test_client, "USB Hub", custom_fields=fields)
        assert defn["custom_fields"] == fields

    def test_create_without_custom_fields_returns_none(self, test_client: TestClient) -> None:
        """POST without custom_fields → response has custom_fields=None."""
        defn = _create_definition(test_client, "Plain Widget")
        assert defn["custom_fields"] is None

    def test_get_returns_parsed_custom_fields(self, test_client: TestClient) -> None:
        """GET /definitions/{id} returns the same parsed dict as POST."""
        fields = {"sku": "ABC-123", "weight_g": 250}
        defn = _create_definition(test_client, "Weighable", custom_fields=fields)
        fetched = _get_definition(test_client, defn["id"])
        assert fetched["custom_fields"] == fields

    def test_patch_updates_custom_fields(self, test_client: TestClient) -> None:
        """PATCH with new custom_fields replaces the stored value."""
        defn = _create_definition(test_client, "Updatable", custom_fields={"old_key": "old_value"})
        resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"custom_fields": {"new_key": "new_value", "count": 42}},
        )
        assert resp.status_code == 200
        assert resp.json()["custom_fields"] == {"new_key": "new_value", "count": 42}

    def test_patch_null_clears_custom_fields(self, test_client: TestClient) -> None:
        """PATCH with custom_fields=null clears the stored value to None."""
        defn = _create_definition(test_client, "Clearable", custom_fields={"to_be_cleared": True})
        resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"custom_fields": None},
        )
        assert resp.status_code == 200
        assert resp.json()["custom_fields"] is None

    def test_patch_omit_leaves_custom_fields_unchanged(self, test_client: TestClient) -> None:
        """PATCH that omits custom_fields leaves existing value unchanged."""
        fields = {"untouched": "value"}
        defn = _create_definition(test_client, "Unchangeable", custom_fields=fields)
        # Patch only the name — custom_fields is NOT in the payload.
        resp = test_client.patch(
            f"/api/definitions/{defn['id']}",
            json={"name": "Unchangeable Renamed"},
        )
        assert resp.status_code == 200
        assert resp.json()["custom_fields"] == fields

    def test_mixed_scalar_types_preserved(self, test_client: TestClient) -> None:
        """str/int/float/bool/null values are all preserved on round-trip."""
        fields = {
            "string_val": "hello",
            "int_val": 42,
            "float_val": 3.14,
            "bool_true": True,
            "bool_false": False,
            "null_val": None,
        }
        defn = _create_definition(test_client, "Mixed Types", custom_fields=fields)
        result = defn["custom_fields"]
        assert result is not None
        assert result["string_val"] == "hello"
        assert result["int_val"] == 42
        # JSON floats may round-trip as float — check close enough.
        assert abs(result["float_val"] - 3.14) < 1e-9
        assert result["bool_true"] is True
        assert result["bool_false"] is False
        assert result["null_val"] is None

    def test_persisted_column_is_json_string(self, test_client: TestClient, temp_db: Path) -> None:
        """The raw column value in SQLite is a JSON string, not a Python dict."""
        from sqlalchemy import create_engine

        fields = {"key": "value"}
        defn = _create_definition(test_client, "DB Check", custom_fields=fields)

        engine = create_engine(f"sqlite:///{temp_db}")
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT custom_fields FROM item_definitions WHERE id = :id"),
                {"id": defn["id"]},
            ).fetchone()
        assert row is not None
        raw = row[0]
        assert isinstance(raw, str), f"expected str, got {type(raw)}"
        parsed = json.loads(raw)
        assert parsed == fields


# ---------------------------------------------------------------------------
# 2. Instance custom fields — happy path
# ---------------------------------------------------------------------------


class TestInstanceCustomFields:
    """Custom fields round-trip tests for stock instances."""

    def _make_defn(self, client: TestClient, suffix: str = "") -> dict:  # type: ignore[type-arg]
        return _create_definition(client, f"InstanceDefn{suffix}")

    def test_create_instance_with_custom_fields(self, test_client: TestClient) -> None:
        """POST /instances with custom_fields → response has parsed dict."""
        defn = self._make_defn(test_client, "_create")
        fields = {"dosage": "10mg", "lot_code": "L2024001"}
        inst = _create_instance(test_client, defn["id"], custom_fields=fields)
        assert inst["custom_fields"] == fields

    def test_create_instance_without_custom_fields_returns_none(
        self, test_client: TestClient
    ) -> None:
        defn = self._make_defn(test_client, "_none")
        inst = _create_instance(test_client, defn["id"])
        assert inst["custom_fields"] is None

    def test_patch_instance_updates_custom_fields(self, test_client: TestClient) -> None:
        defn = self._make_defn(test_client, "_patch")
        inst = _create_instance(test_client, defn["id"], custom_fields={"original": "data"})
        resp = test_client.patch(
            f"/api/instances/{inst['id']}",
            json={"custom_fields": {"updated": True, "count": 7}},
        )
        assert resp.status_code == 200
        assert resp.json()["custom_fields"] == {"updated": True, "count": 7}

    def test_patch_instance_null_clears_custom_fields(self, test_client: TestClient) -> None:
        defn = self._make_defn(test_client, "_clear")
        inst = _create_instance(test_client, defn["id"], custom_fields={"to_clear": "yes"})
        resp = test_client.patch(
            f"/api/instances/{inst['id']}",
            json={"custom_fields": None},
        )
        assert resp.status_code == 200
        assert resp.json()["custom_fields"] is None

    def test_patch_instance_omit_leaves_custom_fields_unchanged(
        self, test_client: TestClient
    ) -> None:
        defn = self._make_defn(test_client, "_omit")
        fields = {"stable": "field"}
        inst = _create_instance(test_client, defn["id"], custom_fields=fields)
        # Patch only serial — custom_fields not in payload.
        resp = test_client.patch(
            f"/api/instances/{inst['id']}",
            json={"serial": "SN12345"},
        )
        assert resp.status_code == 200
        assert resp.json()["custom_fields"] == fields

    def test_instance_mixed_scalar_types(self, test_client: TestClient) -> None:
        defn = self._make_defn(test_client, "_mixed")
        fields = {"s": "text", "i": 99, "f": 2.718, "b": False, "n": None}
        inst = _create_instance(test_client, defn["id"], custom_fields=fields)
        result = inst["custom_fields"]
        assert result is not None
        assert result["s"] == "text"
        assert result["i"] == 99
        assert abs(result["f"] - 2.718) < 1e-9
        assert result["b"] is False
        assert result["n"] is None

    def test_instance_persisted_column_is_json_string(
        self, test_client: TestClient, temp_db: Path
    ) -> None:
        from sqlalchemy import create_engine

        defn = self._make_defn(test_client, "_dbcheck")
        fields = {"inst_key": "inst_val"}
        inst = _create_instance(test_client, defn["id"], custom_fields=fields)

        engine = create_engine(f"sqlite:///{temp_db}")
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT custom_fields FROM stock_instances WHERE id = :id"),
                {"id": inst["id"]},
            ).fetchone()
        assert row is not None
        raw = row[0]
        assert isinstance(raw, str), f"expected str, got {type(raw)}"
        assert json.loads(raw) == fields


# ---------------------------------------------------------------------------
# 3. Validation — rejected inputs
# ---------------------------------------------------------------------------


class TestCustomFieldsValidation:
    """Validation errors for illegal custom_fields inputs."""

    def _def_id(self, client: TestClient) -> int:
        """Helper: create a plain definition and return its id."""
        resp = client.post(
            "/api/definitions",
            json={"name": f"ValDefn-{id(client)}", "stock_tracking_mode": "none"},
        )
        assert resp.status_code == 201
        return resp.json()["id"]  # type: ignore[return-value]

    # --- nested / non-scalar values ----------------------------------------

    def test_nested_dict_value_rejected_on_definition_create(self, test_client: TestClient) -> None:
        """A nested dict as a value → 422."""
        resp = test_client.post(
            "/api/definitions",
            json={
                "name": "Nested Dict",
                "custom_fields": {"bad": {"nested": "dict"}},
            },
        )
        assert resp.status_code == 422

    def test_list_value_rejected_on_definition_create(self, test_client: TestClient) -> None:
        """A list value → 422."""
        resp = test_client.post(
            "/api/definitions",
            json={"name": "List Val", "custom_fields": {"bad": [1, 2, 3]}},
        )
        assert resp.status_code == 422

    def test_nested_dict_value_rejected_on_instance_create(self, test_client: TestClient) -> None:
        def_id = self._def_id(test_client)
        resp = test_client.post(
            "/api/instances",
            json={"definition_id": def_id, "custom_fields": {"nested": {"x": 1}}},
        )
        assert resp.status_code == 422

    def test_list_value_rejected_on_definition_patch(self, test_client: TestClient) -> None:
        def_id = self._def_id(test_client)
        resp = test_client.patch(
            f"/api/definitions/{def_id}",
            json={"custom_fields": {"bad": [1, 2]}},
        )
        assert resp.status_code == 422

    # --- key cap -----------------------------------------------------------

    def test_key_too_long_rejected_on_definition_create(self, test_client: TestClient) -> None:
        """A key with > 64 characters → 422."""
        long_key = "k" * 65  # one over the cap
        resp = test_client.post(
            "/api/definitions",
            json={"name": "Long Key", "custom_fields": {long_key: "value"}},
        )
        assert resp.status_code == 422

    def test_key_exactly_at_cap_accepted(self, test_client: TestClient) -> None:
        """A key with exactly 64 characters is accepted."""
        ok_key = "k" * 64
        resp = test_client.post(
            "/api/definitions",
            json={
                "name": "Max Key",
                "stock_tracking_mode": "none",
                "custom_fields": {ok_key: "value"},
            },
        )
        assert resp.status_code == 201
        assert resp.json()["custom_fields"] == {ok_key: "value"}

    def test_empty_key_rejected(self, test_client: TestClient) -> None:
        """An empty string key → 422."""
        resp = test_client.post(
            "/api/definitions",
            json={"name": "Empty Key", "custom_fields": {"": "value"}},
        )
        assert resp.status_code == 422

    # --- string value cap --------------------------------------------------

    def test_string_value_too_long_rejected(self, test_client: TestClient) -> None:
        """A string value with > 1024 characters → 422."""
        long_val = "v" * 1025  # one over
        resp = test_client.post(
            "/api/definitions",
            json={"name": "Long Value", "custom_fields": {"key": long_val}},
        )
        assert resp.status_code == 422

    def test_string_value_exactly_at_cap_accepted(self, test_client: TestClient) -> None:
        """A string value with exactly 1024 characters is accepted."""
        ok_val = "v" * 1024
        resp = test_client.post(
            "/api/definitions",
            json={
                "name": "Max Val",
                "stock_tracking_mode": "none",
                "custom_fields": {"key": ok_val},
            },
        )
        assert resp.status_code == 201
        assert resp.json()["custom_fields"] == {"key": ok_val}

    # --- field count cap ---------------------------------------------------

    def test_field_count_over_cap_rejected(self, test_client: TestClient) -> None:
        """More than 50 fields → 422."""
        too_many = {f"field_{i}": i for i in range(51)}  # 51 fields
        resp = test_client.post(
            "/api/definitions",
            json={"name": "Too Many", "custom_fields": too_many},
        )
        assert resp.status_code == 422

    def test_field_count_exactly_at_cap_accepted(self, test_client: TestClient) -> None:
        """Exactly 50 fields is accepted."""
        at_cap = {f"field_{i}": i for i in range(50)}  # 50 fields
        resp = test_client.post(
            "/api/definitions",
            json={
                "name": "At Cap",
                "stock_tracking_mode": "none",
                "custom_fields": at_cap,
            },
        )
        assert resp.status_code == 201
        assert len(resp.json()["custom_fields"]) == 50

    # --- non-dict top-level ------------------------------------------------

    def test_non_dict_custom_fields_rejected(self, test_client: TestClient) -> None:
        """custom_fields as a list (not a dict) → 422."""
        resp = test_client.post(
            "/api/definitions",
            json={"name": "List CF", "custom_fields": ["not", "a", "dict"]},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 4. Migration tests
# ---------------------------------------------------------------------------


class TestMigration0025:
    """Migration 0025: add custom_fields to item_definitions."""

    def _run_alembic(self, *args: str, url: str) -> tuple[int, str]:
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

    def test_upgrade_adds_column_and_downgrade_removes_it(self) -> None:
        """0025 upgrade adds the custom_fields column to item_definitions; downgrade removes it."""
        from sqlalchemy import create_engine as sa_create_engine
        from sqlalchemy import inspect as sa_inspect

        fd, path_str = tempfile.mkstemp(suffix=".db", prefix="mig_0025_test_")
        os.close(fd)
        db_path = Path(path_str)
        db_path.unlink()
        url = f"sqlite:///{path_str}"

        try:
            # Upgrade to 0024 first.
            rc, output = self._run_alembic("upgrade", "0024", url=url)
            assert rc == 0, f"alembic upgrade 0024 failed:\n{output}"

            eng = sa_create_engine(url)
            cols_before = {c["name"] for c in sa_inspect(eng).get_columns("item_definitions")}
            eng.dispose()
            assert "custom_fields" not in cols_before

            # Upgrade to 0025.
            rc, output = self._run_alembic("upgrade", "0025", url=url)
            assert rc == 0, f"alembic upgrade 0025 failed:\n{output}"

            eng = sa_create_engine(url)
            cols_after = {c["name"] for c in sa_inspect(eng).get_columns("item_definitions")}
            eng.dispose()
            assert "custom_fields" in cols_after

            # Downgrade back to 0024.
            rc, output = self._run_alembic("downgrade", "0024", url=url)
            assert rc == 0, f"alembic downgrade to 0024 failed:\n{output}"

            eng = sa_create_engine(url)
            cols_down = {c["name"] for c in sa_inspect(eng).get_columns("item_definitions")}
            eng.dispose()
            assert "custom_fields" not in cols_down

        finally:
            if db_path.exists():
                db_path.unlink()

    def test_existing_rows_unaffected_by_upgrade(self) -> None:
        """Rows present at 0024 get NULL custom_fields after 0025 upgrade."""
        from sqlalchemy import create_engine as sa_create_engine
        from sqlalchemy import text as sa_text

        fd, path_str = tempfile.mkstemp(suffix=".db", prefix="mig_0025_rows_")
        os.close(fd)
        db_path = Path(path_str)
        db_path.unlink()
        url = f"sqlite:///{path_str}"

        try:
            # Upgrade to 0024, seed a row, then upgrade to 0025.
            rc, output = self._run_alembic("upgrade", "0024", url=url)
            assert rc == 0, f"alembic upgrade 0024 failed:\n{output}"

            eng = sa_create_engine(url)
            with eng.connect() as conn:
                # Migration 0006 already seeds item_kinds; just grab the first id.
                row = conn.execute(sa_text("SELECT id FROM item_kinds LIMIT 1")).fetchone()
                assert row is not None, "item_kinds not seeded by migration 0006"
                kind_id = row[0]
                conn.execute(
                    sa_text(
                        "INSERT INTO item_definitions "
                        "(name, kind_id, unit, stock_tracking_mode) "
                        "VALUES ('existing_def', :kid, 'pcs', 'none')"
                    ),
                    {"kid": kind_id},
                )
                conn.commit()
            eng.dispose()

            # Upgrade to 0025.
            rc, output = self._run_alembic("upgrade", "0025", url=url)
            assert rc == 0, f"alembic upgrade 0025 failed:\n{output}"

            eng = sa_create_engine(url)
            with eng.connect() as conn:
                row = conn.execute(
                    sa_text("SELECT custom_fields FROM item_definitions WHERE name='existing_def'")
                ).fetchone()
            eng.dispose()

            assert row is not None
            assert row[0] is None  # existing rows get NULL (no backfill)

        finally:
            if db_path.exists():
                db_path.unlink()


class TestMigration0026:
    """Migration 0026: add custom_fields to stock_instances."""

    def _run_alembic(self, *args: str, url: str) -> tuple[int, str]:
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

    def test_upgrade_adds_column_and_downgrade_removes_it(self) -> None:
        """0026 upgrade adds custom_fields to stock_instances; downgrade removes it."""
        from sqlalchemy import create_engine as sa_create_engine
        from sqlalchemy import inspect as sa_inspect

        fd, path_str = tempfile.mkstemp(suffix=".db", prefix="mig_0026_test_")
        os.close(fd)
        db_path = Path(path_str)
        db_path.unlink()
        url = f"sqlite:///{path_str}"

        try:
            # Upgrade to 0025.
            rc, output = self._run_alembic("upgrade", "0025", url=url)
            assert rc == 0, f"alembic upgrade 0025 failed:\n{output}"

            eng = sa_create_engine(url)
            cols_before = {c["name"] for c in sa_inspect(eng).get_columns("stock_instances")}
            eng.dispose()
            assert "custom_fields" not in cols_before

            # Upgrade to 0026.
            rc, output = self._run_alembic("upgrade", "0026", url=url)
            assert rc == 0, f"alembic upgrade 0026 failed:\n{output}"

            eng = sa_create_engine(url)
            cols_after = {c["name"] for c in sa_inspect(eng).get_columns("stock_instances")}
            eng.dispose()
            assert "custom_fields" in cols_after

            # Downgrade back to 0025.
            rc, output = self._run_alembic("downgrade", "0025", url=url)
            assert rc == 0, f"alembic downgrade to 0025 failed:\n{output}"

            eng = sa_create_engine(url)
            cols_down = {c["name"] for c in sa_inspect(eng).get_columns("stock_instances")}
            eng.dispose()
            assert "custom_fields" not in cols_down

        finally:
            if db_path.exists():
                db_path.unlink()

    def test_existing_rows_unaffected_by_upgrade(self) -> None:
        """Rows present at 0025 get NULL custom_fields after 0026 upgrade."""
        from sqlalchemy import create_engine as sa_create_engine
        from sqlalchemy import text as sa_text

        fd, path_str = tempfile.mkstemp(suffix=".db", prefix="mig_0026_rows_")
        os.close(fd)
        db_path = Path(path_str)
        db_path.unlink()
        url = f"sqlite:///{path_str}"

        try:
            # Upgrade to 0025, seed rows, then upgrade to 0026.
            rc, output = self._run_alembic("upgrade", "0025", url=url)
            assert rc == 0, f"alembic upgrade 0025 failed:\n{output}"

            eng = sa_create_engine(url)
            with eng.connect() as conn:
                # Migration 0006 already seeds item_kinds.
                row = conn.execute(sa_text("SELECT id FROM item_kinds LIMIT 1")).fetchone()
                assert row is not None
                kind_id = row[0]
                conn.execute(
                    sa_text(
                        "INSERT INTO item_definitions "
                        "(name, kind_id, unit, stock_tracking_mode) "
                        "VALUES ('test_def', :kid, 'pcs', 'none')"
                    ),
                    {"kid": kind_id},
                )
                # Get the new definition id.
                def_row = conn.execute(
                    sa_text("SELECT id FROM item_definitions WHERE name='test_def'")
                ).fetchone()
                assert def_row is not None
                def_id = def_row[0]
                # stock_instances: only definition_id is required (others have defaults or nullable).
                conn.execute(
                    sa_text("INSERT INTO stock_instances (definition_id) VALUES (:did)"),
                    {"did": def_id},
                )
                conn.commit()
            eng.dispose()

            # Upgrade to 0026.
            rc, output = self._run_alembic("upgrade", "0026", url=url)
            assert rc == 0, f"alembic upgrade 0026 failed:\n{output}"

            eng = sa_create_engine(url)
            with eng.connect() as conn:
                row = conn.execute(
                    sa_text("SELECT custom_fields FROM stock_instances LIMIT 1")
                ).fetchone()
            eng.dispose()

            assert row is not None
            assert row[0] is None  # existing rows get NULL (no backfill)

        finally:
            if db_path.exists():
                db_path.unlink()


# ---------------------------------------------------------------------------
# 5. Unit tests for the CustomFieldsModel validator (standalone)
# ---------------------------------------------------------------------------


class TestCustomFieldsValidator:
    """Unit tests for the validation logic in custom_fields.py."""

    def test_valid_map_passes(self) -> None:
        from app.schemas.custom_fields import _validate_custom_fields

        result = _validate_custom_fields({"s": "hello", "i": 1, "f": 1.5, "b": True, "n": None})
        assert result == {"s": "hello", "i": 1, "f": 1.5, "b": True, "n": None}

    def test_nested_dict_raises(self) -> None:
        from app.schemas.custom_fields import _validate_custom_fields

        with pytest.raises(ValueError, match="scalar"):
            _validate_custom_fields({"bad": {"nested": True}})

    def test_list_value_raises(self) -> None:
        from app.schemas.custom_fields import _validate_custom_fields

        with pytest.raises(ValueError, match="scalar"):
            _validate_custom_fields({"bad": [1, 2, 3]})

    def test_empty_key_raises(self) -> None:
        from app.schemas.custom_fields import _validate_custom_fields

        with pytest.raises(ValueError, match="empty"):
            _validate_custom_fields({"": "value"})

    def test_key_too_long_raises(self) -> None:
        from app.schemas.custom_fields import _validate_custom_fields

        with pytest.raises(ValueError, match="64"):
            _validate_custom_fields({"k" * 65: "value"})

    def test_value_string_too_long_raises(self) -> None:
        from app.schemas.custom_fields import _validate_custom_fields

        with pytest.raises(ValueError, match="1024"):
            _validate_custom_fields({"key": "v" * 1025})

    def test_count_over_cap_raises(self) -> None:
        from app.schemas.custom_fields import _validate_custom_fields

        with pytest.raises(ValueError, match="50"):
            _validate_custom_fields({f"k{i}": i for i in range(51)})

    def test_non_dict_raises(self) -> None:
        from app.schemas.custom_fields import _validate_custom_fields

        with pytest.raises(ValueError, match="dict"):
            _validate_custom_fields([1, 2, 3])

    def test_serialize_round_trip(self) -> None:
        from app.schemas.custom_fields import deserialize_custom_fields, serialize_custom_fields

        fields = {"z": 1, "a": "hello", "m": None}
        raw = serialize_custom_fields(fields)
        assert raw is not None
        assert isinstance(raw, str)
        # Keys are sorted.
        assert raw == '{"a":"hello","m":null,"z":1}'

        restored = deserialize_custom_fields(raw)
        assert restored == fields

    def test_serialize_none_returns_none(self) -> None:
        from app.schemas.custom_fields import serialize_custom_fields

        assert serialize_custom_fields(None) is None

    def test_deserialize_none_returns_none(self) -> None:
        from app.schemas.custom_fields import deserialize_custom_fields

        assert deserialize_custom_fields(None) is None

    def test_deserialize_empty_string_returns_none(self) -> None:
        from app.schemas.custom_fields import deserialize_custom_fields

        assert deserialize_custom_fields("") is None

    def test_deserialize_invalid_json_returns_none(self) -> None:
        from app.schemas.custom_fields import deserialize_custom_fields

        result = deserialize_custom_fields("{not valid json")
        assert result is None
