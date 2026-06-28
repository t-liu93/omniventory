"""Tests for M5 Step 3: generic notes with polymorphic links.

Coverage
--------
- CRUD: create / get-by-list / update / delete notes.
- List is scoped to the owner: notes on owner A are invisible when listing
  owner B (even with the same model_type).
- Foreign / missing note id → ``note.not_found`` (404) on PATCH and DELETE.
- Owner-delete cascade removes all the owner's notes for all THREE owner types
  (item_definition, stock_instance, location).
- Bad ``model_type`` → ``validation.invalid_input`` (422) on POST.
- Missing owner → owner's not-found code on POST.
- ``updated_at`` refreshes on update (it changes vs ``created_at``).
- Migration ``0024`` upgrade + downgrade cleanly on a DB at ``0023``.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Fixture infrastructure (mirrors the pattern from test_m5_step2.py)
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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m5_step3_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m5-step3")
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
    """TestClient with full schema (all models), authenticated admin, and isolated media dir."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

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
    import app.models.notification as notif_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as stock_movement_mod
    import app.models.tag as tag_mod
    import app.models.user as user_mod

    # Re-register all models (including new note model) with a fresh Base.
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
    importlib.reload(audit_log_mod)

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


def _create_note(
    client: TestClient,
    model_type: str,
    model_id: int,
    body: str,
) -> dict:  # type: ignore[type-arg]
    resp = client.post(
        "/api/notes",
        json={"model_type": model_type, "model_id": model_id, "body": body},
    )
    assert resp.status_code == 201, f"create_note failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _list_notes(client: TestClient, model_type: str, model_id: int) -> list[dict]:  # type: ignore[type-arg]
    resp = client.get("/api/notes", params={"model_type": model_type, "model_id": model_id})
    assert resp.status_code == 200, f"list_notes failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_definition(client: TestClient, name: str) -> dict:  # type: ignore[type-arg]
    resp = client.post("/api/definitions", json={"name": name})
    assert resp.status_code == 201, f"create_definition failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_location(client: TestClient, name: str) -> dict:  # type: ignore[type-arg]
    resp = client.post("/api/locations", json={"name": name})
    assert resp.status_code == 201, f"create_location failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_instance(client: TestClient) -> dict:  # type: ignore[type-arg]
    """Create a 'none' stock-tracking definition + instance."""
    defn_resp = client.post(
        "/api/definitions",
        json={"name": f"InstanceDefn-{id(client)}", "stock_tracking_mode": "none"},
    )
    assert defn_resp.status_code == 201
    defn_id = defn_resp.json()["id"]
    inst_resp = client.post("/api/instances", json={"definition_id": defn_id})
    assert inst_resp.status_code == 201, f"create_instance failed: {inst_resp.json()}"
    return inst_resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. Note CRUD — happy path
# ---------------------------------------------------------------------------


class TestNoteCRUD:
    """Basic note creation, list, patch, and delete."""

    def test_create_note_returns_201(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "CRUD Widget")
        resp = test_client.post(
            "/api/notes",
            json={"model_type": "item_definition", "model_id": defn["id"], "body": "Hello note"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["model_type"] == "item_definition"
        assert body["model_id"] == defn["id"]
        assert body["body"] == "Hello note"
        assert "id" in body
        assert "created_at" in body
        assert "updated_at" in body
        # created_by should be set to the authenticated user (non-null)
        assert body["created_by"] is not None

    def test_list_notes_returns_chronological_order(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Ordered Widget")
        note_a = _create_note(test_client, "item_definition", defn["id"], "First note")
        note_b = _create_note(test_client, "item_definition", defn["id"], "Second note")

        notes = _list_notes(test_client, "item_definition", defn["id"])
        assert len(notes) == 2
        bodies = [n["body"] for n in notes]
        assert bodies == ["First note", "Second note"]
        assert notes[0]["id"] == note_a["id"]
        assert notes[1]["id"] == note_b["id"]

    def test_patch_note_body(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Patch Widget")
        note = _create_note(test_client, "item_definition", defn["id"], "Original body")

        resp = test_client.patch(f"/api/notes/{note['id']}", json={"body": "Updated body"})
        assert resp.status_code == 200
        assert resp.json()["body"] == "Updated body"

    def test_delete_note_returns_204(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Delete Widget")
        note = _create_note(test_client, "item_definition", defn["id"], "To be deleted")

        resp = test_client.delete(f"/api/notes/{note['id']}")
        assert resp.status_code == 204

        # Should be gone from the list now.
        notes = _list_notes(test_client, "item_definition", defn["id"])
        assert all(n["id"] != note["id"] for n in notes)

    def test_create_note_on_location(self, test_client: TestClient) -> None:
        loc = _create_location(test_client, "Note Location")
        note = _create_note(test_client, "location", loc["id"], "Location note")
        assert note["model_type"] == "location"
        assert note["model_id"] == loc["id"]

    def test_create_note_on_stock_instance(self, test_client: TestClient) -> None:
        inst = _create_instance(test_client)
        note = _create_note(test_client, "stock_instance", inst["id"], "Instance note")
        assert note["model_type"] == "stock_instance"
        assert note["model_id"] == inst["id"]


# ---------------------------------------------------------------------------
# 2. List is scoped to the owner
# ---------------------------------------------------------------------------


class TestNoteOwnerScoping:
    """Notes on owner A are invisible when listing owner B."""

    def test_notes_scoped_to_owner(self, test_client: TestClient) -> None:
        defn_a = _create_definition(test_client, "Owner A")
        defn_b = _create_definition(test_client, "Owner B")

        _create_note(test_client, "item_definition", defn_a["id"], "Note on A")
        _create_note(test_client, "item_definition", defn_a["id"], "Another note on A")
        _create_note(test_client, "item_definition", defn_b["id"], "Note on B")

        notes_a = _list_notes(test_client, "item_definition", defn_a["id"])
        notes_b = _list_notes(test_client, "item_definition", defn_b["id"])

        assert len(notes_a) == 2
        assert len(notes_b) == 1
        assert all(n["model_id"] == defn_a["id"] for n in notes_a)
        assert notes_b[0]["model_id"] == defn_b["id"]

    def test_list_unknown_owner_returns_empty(self, test_client: TestClient) -> None:
        """Listing notes for a non-existent owner returns empty (not 404)."""
        notes = _list_notes(test_client, "item_definition", 99999)
        assert notes == []

    def test_notes_across_owner_types_are_independent(self, test_client: TestClient) -> None:
        """A definition and a location with the same id share no notes."""
        defn = _create_definition(test_client, "Type Scope Defn")
        loc = _create_location(test_client, "Type Scope Loc")

        # Deliberately give both the same model_id (if they happen to share one).
        # Even so, different model_types should produce independent lists.
        _create_note(test_client, "item_definition", defn["id"], "Defn note")
        _create_note(test_client, "location", loc["id"], "Loc note")

        defn_notes = _list_notes(test_client, "item_definition", defn["id"])
        loc_notes = _list_notes(test_client, "location", loc["id"])

        assert all(n["model_type"] == "item_definition" for n in defn_notes)
        assert all(n["model_type"] == "location" for n in loc_notes)


# ---------------------------------------------------------------------------
# 3. Not-found guard (note.not_found)
# ---------------------------------------------------------------------------


class TestNoteNotFound:
    """Missing note id on PATCH / DELETE → note.not_found (404)."""

    def test_patch_missing_note_returns_404(self, test_client: TestClient) -> None:
        resp = test_client.patch("/api/notes/99999", json={"body": "ghost"})
        assert resp.status_code == 404
        assert resp.json()["code"] == "note.not_found"

    def test_delete_missing_note_returns_404(self, test_client: TestClient) -> None:
        resp = test_client.delete("/api/notes/99999")
        assert resp.status_code == 404
        assert resp.json()["code"] == "note.not_found"

    def test_delete_already_deleted_note_returns_404(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Already Gone")
        note = _create_note(test_client, "item_definition", defn["id"], "Temporary")

        # First delete succeeds.
        assert test_client.delete(f"/api/notes/{note['id']}").status_code == 204
        # Second delete returns 404.
        resp = test_client.delete(f"/api/notes/{note['id']}")
        assert resp.status_code == 404
        assert resp.json()["code"] == "note.not_found"


# ---------------------------------------------------------------------------
# 4. Bad model_type → validation.invalid_input (422)
# ---------------------------------------------------------------------------


class TestBadModelType:
    """POST /notes with an invalid model_type returns 422 (validation.invalid_input)."""

    def test_invalid_model_type_returns_422(self, test_client: TestClient) -> None:
        resp = test_client.post(
            "/api/notes",
            json={"model_type": "not_a_real_type", "model_id": 1, "body": "Test"},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_empty_model_type_returns_422(self, test_client: TestClient) -> None:
        """Empty model_type fails Pydantic min_length=1 → 422."""
        resp = test_client.post(
            "/api/notes",
            json={"model_type": "", "model_id": 1, "body": "Test"},
        )
        assert resp.status_code == 422

    def test_valid_model_types_are_accepted(self, test_client: TestClient) -> None:
        """All three valid model_types are accepted (owner must exist too)."""
        defn = _create_definition(test_client, "Type Acceptance")
        loc = _create_location(test_client, "Type Acceptance Loc")
        inst = _create_instance(test_client)

        resp_defn = test_client.post(
            "/api/notes",
            json={"model_type": "item_definition", "model_id": defn["id"], "body": "ok"},
        )
        assert resp_defn.status_code == 201

        resp_loc = test_client.post(
            "/api/notes",
            json={"model_type": "location", "model_id": loc["id"], "body": "ok"},
        )
        assert resp_loc.status_code == 201

        resp_inst = test_client.post(
            "/api/notes",
            json={"model_type": "stock_instance", "model_id": inst["id"], "body": "ok"},
        )
        assert resp_inst.status_code == 201


# ---------------------------------------------------------------------------
# 5. Missing owner → owner's not-found code
# ---------------------------------------------------------------------------


class TestMissingOwner:
    """POST /notes with a valid model_type but a non-existent owner returns the owner's 404."""

    def test_missing_definition_owner_returns_404(self, test_client: TestClient) -> None:
        resp = test_client.post(
            "/api/notes",
            json={"model_type": "item_definition", "model_id": 99999, "body": "orphan"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "item_definition.not_found"

    def test_missing_location_owner_returns_404(self, test_client: TestClient) -> None:
        resp = test_client.post(
            "/api/notes",
            json={"model_type": "location", "model_id": 99999, "body": "orphan"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "location.not_found"

    def test_missing_stock_instance_owner_returns_404(self, test_client: TestClient) -> None:
        resp = test_client.post(
            "/api/notes",
            json={"model_type": "stock_instance", "model_id": 99999, "body": "orphan"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "stock_instance.not_found"


# ---------------------------------------------------------------------------
# 6. updated_at refreshes on update
# ---------------------------------------------------------------------------


class TestUpdatedAtRefreshes:
    """Patching a note's body must refresh updated_at (it changes vs created_at)."""

    def test_updated_at_changes_after_patch(self, test_client: TestClient) -> None:
        import time

        defn = _create_definition(test_client, "Timestamp Widget")
        note = _create_note(test_client, "item_definition", defn["id"], "Original")

        created_at = note["created_at"]
        updated_at_before = note["updated_at"]

        # Sleep to ensure timestamps differ.
        # SQLite's CURRENT_TIMESTAMP / func.now() has 1-second resolution,
        # so we need to wait more than 1 second.
        time.sleep(1.1)

        patch_resp = test_client.patch(f"/api/notes/{note['id']}", json={"body": "Updated"})
        assert patch_resp.status_code == 200
        data = patch_resp.json()

        updated_at_after = data["updated_at"]

        # created_at must not change.
        assert data["created_at"] == created_at

        # updated_at must have moved forward.
        assert updated_at_after > updated_at_before, (
            f"updated_at did not advance: before={updated_at_before!r}, after={updated_at_after!r}"
        )


# ---------------------------------------------------------------------------
# 7. Owner-delete cascade removes the owner's notes
# ---------------------------------------------------------------------------


class TestOwnerDeleteCascade:
    """Deleting an owner must remove all its notes (service-layer cascade)."""

    def test_definition_delete_removes_notes(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "CascadeDefn")
        note = _create_note(test_client, "item_definition", defn["id"], "Will be cascaded")

        del_resp = test_client.delete(f"/api/definitions/{defn['id']}")
        assert del_resp.status_code == 204

        # The note must be gone — the API simply returns an empty list now.
        notes = _list_notes(test_client, "item_definition", defn["id"])
        assert all(n["id"] != note["id"] for n in notes)

    def test_location_delete_removes_notes(self, test_client: TestClient) -> None:
        loc = _create_location(test_client, "CascadeLoc")
        note = _create_note(test_client, "location", loc["id"], "Location cascade note")

        del_resp = test_client.delete(f"/api/locations/{loc['id']}")
        assert del_resp.status_code == 204

        notes = _list_notes(test_client, "location", loc["id"])
        assert all(n["id"] != note["id"] for n in notes)

    def test_stock_instance_delete_removes_notes(self, test_client: TestClient) -> None:
        inst = _create_instance(test_client)
        note = _create_note(test_client, "stock_instance", inst["id"], "Instance cascade note")

        del_resp = test_client.delete(f"/api/instances/{inst['id']}")
        assert del_resp.status_code == 204

        notes = _list_notes(test_client, "stock_instance", inst["id"])
        assert all(n["id"] != note["id"] for n in notes)

    def test_cascade_removes_multiple_notes(self, test_client: TestClient) -> None:
        """Deleting an owner removes all its notes (not just the first)."""
        defn = _create_definition(test_client, "MultiNoteCascade")
        _create_note(test_client, "item_definition", defn["id"], "Note 1")
        _create_note(test_client, "item_definition", defn["id"], "Note 2")
        _create_note(test_client, "item_definition", defn["id"], "Note 3")

        assert len(_list_notes(test_client, "item_definition", defn["id"])) == 3

        del_resp = test_client.delete(f"/api/definitions/{defn['id']}")
        assert del_resp.status_code == 204

        notes = _list_notes(test_client, "item_definition", defn["id"])
        assert len(notes) == 0

    def test_cascade_does_not_affect_notes_on_other_owners(self, test_client: TestClient) -> None:
        """Deleting owner A must leave owner B's notes intact."""
        defn_a = _create_definition(test_client, "CascadeA")
        defn_b = _create_definition(test_client, "CascadeB")

        _create_note(test_client, "item_definition", defn_a["id"], "Note on A")
        note_b = _create_note(test_client, "item_definition", defn_b["id"], "Note on B")

        test_client.delete(f"/api/definitions/{defn_a['id']}")

        # Owner B's note must survive.
        notes_b = _list_notes(test_client, "item_definition", defn_b["id"])
        assert any(n["id"] == note_b["id"] for n in notes_b)


# ---------------------------------------------------------------------------
# 8. Migration round-trip (0024)
# ---------------------------------------------------------------------------


class TestMigration0024:
    """Migration 0024 (notes) upgrades and downgrades cleanly on a DB at 0023."""

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

    def test_migration_0024_up_down(self) -> None:
        """Upgrade through 0024; downgrade back to 0023 cleanly."""
        from sqlalchemy import create_engine as sa_create_engine
        from sqlalchemy import inspect as sa_inspect

        fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_mig_0024_")
        os.close(fd)
        db_path = Path(path_str)
        db_path.unlink()
        url = f"sqlite:///{path_str}"

        try:
            # Upgrade to HEAD (applies 0024).
            rc, output = self._run_alembic("upgrade", "head", url=url)
            assert rc == 0, f"alembic upgrade head failed:\n{output}"

            eng = sa_create_engine(url)
            tables = set(sa_inspect(eng).get_table_names())
            eng.dispose()
            assert "notes" in tables, f"notes table missing after upgrade. Tables: {tables}"

            # Downgrade to 0023 — removes notes.
            rc, output = self._run_alembic("downgrade", "0023", url=url)
            assert rc == 0, f"alembic downgrade to 0023 failed:\n{output}"

            eng = sa_create_engine(url)
            tables = set(sa_inspect(eng).get_table_names())
            eng.dispose()
            assert "notes" not in tables, "notes table must be gone after downgrade to 0023"
            assert "tag_links" in tables, "tag_links must survive downgrade to 0023"

        finally:
            if db_path.exists():
                db_path.unlink()
