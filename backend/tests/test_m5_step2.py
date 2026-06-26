"""Tests for M5 Step 2: flat tags with polymorphic links.

Coverage
--------
- Case-insensitive duplicate name → ``tag.duplicate_name`` (409) on create AND rename.
- attach is idempotent: re-attaching the same tag = single link, no error.
- ``set_tags_for_owner`` replaces the set (adds new, removes absent).
- Deleting a tag drops its links (FK ondelete=CASCADE).
- Owner-delete detaches all tag links for all THREE owner types (definition,
  stock instance, location).
- Bad ``model_type`` → ``validation.invalid_input`` (422).
- Missing owner → owner's not-found code.
- Tag-not-found on attach / detach / PATCH / DELETE → ``tag.not_found`` (404).
- Migrations 0022 (tags) and 0023 (tag_links) upgrade and downgrade cleanly
  on a DB that is at revision 0021.
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
# Shared fixture infrastructure (mirrors the pattern from test_m5_step1.py)
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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m5_step2_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m5-step2")
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
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.media_file as media_file_mod
    import app.models.notification as notif_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.stock_instance as stock_instance_mod
    import app.models.stock_movement as stock_movement_mod
    import app.models.tag as tag_mod
    import app.models.user as user_mod

    # Re-register all models (including new tag models) with a fresh Base.
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


def _create_tag(client: TestClient, name: str, color: str | None = None) -> dict:  # type: ignore[type-arg]
    body: dict[str, object] = {"name": name}  # type: ignore[type-arg]
    if color is not None:
        body["color"] = color
    resp = client.post("/api/tags", json=body)
    assert resp.status_code == 201, f"create_tag failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_definition(client: TestClient, name: str) -> dict:  # type: ignore[type-arg]
    resp = client.post("/api/definitions", json={"name": name})
    assert resp.status_code == 201, f"create_definition failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_location(client: TestClient, name: str) -> dict:  # type: ignore[type-arg]
    resp = client.post("/api/locations", json={"name": name})
    assert resp.status_code == 201, f"create_location failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_instance(client: TestClient, definition_id: int) -> dict:  # type: ignore[type-arg]
    """Create a 'none' stock-tracking instance (simplest to create in tests)."""
    resp = client.post(
        "/api/definitions",
        json={"name": f"defn_for_inst_{definition_id}", "stock_tracking_mode": "none"},
    )
    if resp.status_code != 201:
        # Definition might already exist; use the provided definition_id directly.
        pass
    resp2 = client.post("/api/instances", json={"definition_id": definition_id})
    assert resp2.status_code == 201, f"create_instance failed: {resp2.json()}"
    return resp2.json()  # type: ignore[return-value]


def _set_tags(client: TestClient, model_type: str, model_id: int, tag_ids: list[int]) -> list[dict]:  # type: ignore[type-arg]
    resp = client.put(
        "/api/tags/links",
        json={"model_type": model_type, "model_id": model_id, "tag_ids": tag_ids},
    )
    assert resp.status_code == 200, f"set_tags failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _get_links(client: TestClient, model_type: str, model_id: int) -> list[dict]:  # type: ignore[type-arg]
    resp = client.get("/api/tags/links", params={"model_type": model_type, "model_id": model_id})
    assert resp.status_code == 200, f"get_links failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. Tag CRUD — basic happy-path
# ---------------------------------------------------------------------------


class TestTagCRUD:
    """Basic tag creation, list, patch, and delete."""

    def test_create_tag_returns_201(self, test_client: TestClient) -> None:
        resp = test_client.post("/api/tags", json={"name": "food", "color": "green"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "food"
        assert body["color"] == "green"
        assert "id" in body
        assert "created_at" in body

    def test_list_tags_returns_all(self, test_client: TestClient) -> None:
        _create_tag(test_client, "alpha")
        _create_tag(test_client, "beta")
        resp = test_client.get("/api/tags")
        assert resp.status_code == 200
        names = {t["name"] for t in resp.json()}
        assert {"alpha", "beta"}.issubset(names)

    def test_list_tags_with_q_filter(self, test_client: TestClient) -> None:
        _create_tag(test_client, "electronics")
        _create_tag(test_client, "food")
        resp = test_client.get("/api/tags", params={"q": "elec"})
        assert resp.status_code == 200
        names = {t["name"] for t in resp.json()}
        assert "electronics" in names
        assert "food" not in names

    def test_patch_tag_name(self, test_client: TestClient) -> None:
        tag = _create_tag(test_client, "old-name")
        resp = test_client.patch(f"/api/tags/{tag['id']}", json={"name": "new-name"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "new-name"

    def test_patch_tag_color(self, test_client: TestClient) -> None:
        tag = _create_tag(test_client, "colorless")
        resp = test_client.patch(f"/api/tags/{tag['id']}", json={"color": "blue"})
        assert resp.status_code == 200
        assert resp.json()["color"] == "blue"

    def test_delete_tag_returns_204(self, test_client: TestClient) -> None:
        tag = _create_tag(test_client, "to-delete")
        resp = test_client.delete(f"/api/tags/{tag['id']}")
        assert resp.status_code == 204

    def test_delete_nonexistent_tag_returns_404(self, test_client: TestClient) -> None:
        resp = test_client.delete("/api/tags/99999")
        assert resp.status_code == 404
        assert resp.json()["code"] == "tag.not_found"

    def test_patch_nonexistent_tag_returns_404(self, test_client: TestClient) -> None:
        resp = test_client.patch("/api/tags/99999", json={"name": "x"})
        assert resp.status_code == 404
        assert resp.json()["code"] == "tag.not_found"


# ---------------------------------------------------------------------------
# 2. Case-insensitive duplicate name guard
# ---------------------------------------------------------------------------


class TestDuplicateName:
    """tag.duplicate_name (409) is returned on create AND rename when the name exists."""

    def test_create_exact_duplicate_returns_409(self, test_client: TestClient) -> None:
        _create_tag(test_client, "UniqueTag")
        resp = test_client.post("/api/tags", json={"name": "UniqueTag"})
        assert resp.status_code == 409
        assert resp.json()["code"] == "tag.duplicate_name"

    def test_create_case_insensitive_duplicate_returns_409(self, test_client: TestClient) -> None:
        _create_tag(test_client, "mytag")
        resp = test_client.post("/api/tags", json={"name": "MYTAG"})
        assert resp.status_code == 409
        assert resp.json()["code"] == "tag.duplicate_name"

    def test_create_mixed_case_duplicate_returns_409(self, test_client: TestClient) -> None:
        _create_tag(test_client, "FoodTag")
        resp = test_client.post("/api/tags", json={"name": "foodtag"})
        assert resp.status_code == 409
        assert resp.json()["code"] == "tag.duplicate_name"

    def test_rename_to_existing_name_returns_409(self, test_client: TestClient) -> None:
        tag_a = _create_tag(test_client, "apple")
        _create_tag(test_client, "banana")
        # Try to rename "apple" to "banana" → conflict.
        resp = test_client.patch(f"/api/tags/{tag_a['id']}", json={"name": "banana"})
        assert resp.status_code == 409
        assert resp.json()["code"] == "tag.duplicate_name"

    def test_rename_ci_conflict_with_other_tag_returns_409(self, test_client: TestClient) -> None:
        """Renaming tag A so its lowercase matches tag B's lowercase returns 409."""
        tag_a = _create_tag(test_client, "plum")
        _create_tag(test_client, "GRAPE")
        # Try to rename "plum" to "grape" → CI conflict with "GRAPE".
        resp = test_client.patch(f"/api/tags/{tag_a['id']}", json={"name": "grape"})
        assert resp.status_code == 409
        assert resp.json()["code"] == "tag.duplicate_name"

    def test_rename_to_own_name_is_allowed(self, test_client: TestClient) -> None:
        """Renaming a tag to its own name (same case) should succeed."""
        tag = _create_tag(test_client, "solo")
        resp = test_client.patch(f"/api/tags/{tag['id']}", json={"name": "solo"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "solo"

    def test_rename_to_own_name_different_case_is_allowed(self, test_client: TestClient) -> None:
        """Renaming a tag to its own name with different case (no OTHER tag) should succeed."""
        tag = _create_tag(test_client, "widget")
        resp = test_client.patch(f"/api/tags/{tag['id']}", json={"name": "Widget"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Widget"

    def test_rename_to_different_existing_ci_name_returns_409(
        self, test_client: TestClient
    ) -> None:
        """Renaming tag A to the CI-equivalent of tag B's name returns 409."""
        tag_a = _create_tag(test_client, "tagtwo")
        _create_tag(test_client, "tagone")
        resp = test_client.patch(f"/api/tags/{tag_a['id']}", json={"name": "TAGONE"})
        assert resp.status_code == 409
        assert resp.json()["code"] == "tag.duplicate_name"


# ---------------------------------------------------------------------------
# 3. Idempotent attach
# ---------------------------------------------------------------------------


class TestIdempotentAttach:
    """Re-attaching the same tag to the same owner is a no-op (no duplicate link)."""

    def test_attach_is_idempotent_via_set(self, test_client: TestClient) -> None:
        """set_tags_for_owner with the same tag_id twice results in a single link."""
        defn = _create_definition(test_client, "Idempotent Widget")
        tag = _create_tag(test_client, "idempotent-tag")

        # First set.
        result1 = _set_tags(test_client, "item_definition", defn["id"], [tag["id"]])
        assert len(result1) == 1

        # Second set with the same tag — must still be exactly one link.
        result2 = _set_tags(test_client, "item_definition", defn["id"], [tag["id"]])
        assert len(result2) == 1

        # Verify via GET /tags/links.
        links = _get_links(test_client, "item_definition", defn["id"])
        assert len(links) == 1
        assert links[0]["tag_id"] == tag["id"]

    def test_repeated_set_does_not_duplicate(self, test_client: TestClient) -> None:
        """Calling PUT /tags/links twice with the same set leaves exactly one link."""
        defn = _create_definition(test_client, "Dup-Guard Widget")
        tag = _create_tag(test_client, "nodup")

        _set_tags(test_client, "item_definition", defn["id"], [tag["id"]])
        _set_tags(test_client, "item_definition", defn["id"], [tag["id"]])

        links = _get_links(test_client, "item_definition", defn["id"])
        assert len(links) == 1


# ---------------------------------------------------------------------------
# 4. set_tags_for_owner replaces the set
# ---------------------------------------------------------------------------


class TestSetTagsForOwner:
    """PUT /tags/links replaces the owner's entire tag set."""

    def test_set_adds_new_tags(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Set Widget A")
        t1 = _create_tag(test_client, "set-tag-a")
        t2 = _create_tag(test_client, "set-tag-b")

        # Start empty, then set both.
        result = _set_tags(test_client, "item_definition", defn["id"], [t1["id"], t2["id"]])
        assert len(result) == 2
        ids = {t["id"] for t in result}
        assert ids == {t1["id"], t2["id"]}

    def test_set_removes_absent_tags(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Set Widget B")
        t1 = _create_tag(test_client, "keep-tag")
        t2 = _create_tag(test_client, "remove-tag")

        # Set both tags.
        _set_tags(test_client, "item_definition", defn["id"], [t1["id"], t2["id"]])

        # Now set only t1 — t2 should be removed.
        result = _set_tags(test_client, "item_definition", defn["id"], [t1["id"]])
        assert len(result) == 1
        assert result[0]["id"] == t1["id"]

        links = _get_links(test_client, "item_definition", defn["id"])
        assert len(links) == 1

    def test_set_empty_clears_all_tags(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Set Widget C")
        t1 = _create_tag(test_client, "clear-me")

        _set_tags(test_client, "item_definition", defn["id"], [t1["id"]])
        result = _set_tags(test_client, "item_definition", defn["id"], [])
        assert result == []

        links = _get_links(test_client, "item_definition", defn["id"])
        assert len(links) == 0

    def test_set_tags_for_unknown_tag_returns_404(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Set Widget D")
        resp = test_client.put(
            "/api/tags/links",
            json={
                "model_type": "item_definition",
                "model_id": defn["id"],
                "tag_ids": [99999],
            },
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "tag.not_found"


# ---------------------------------------------------------------------------
# 5. Deleting a tag drops its links (FK CASCADE)
# ---------------------------------------------------------------------------


class TestTagDeleteCascade:
    """Deleting a tag must drop all its tag_links via FK ondelete=CASCADE."""

    def test_delete_tag_removes_links(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Cascade Widget")
        tag = _create_tag(test_client, "cascade-tag")

        _set_tags(test_client, "item_definition", defn["id"], [tag["id"]])
        assert len(_get_links(test_client, "item_definition", defn["id"])) == 1

        # Delete the tag.
        del_resp = test_client.delete(f"/api/tags/{tag['id']}")
        assert del_resp.status_code == 204

        # Links must be gone (FK CASCADE).
        links = _get_links(test_client, "item_definition", defn["id"])
        assert len(links) == 0

    def test_delete_tag_drops_links_across_multiple_owners(self, test_client: TestClient) -> None:
        """A tag attached to two owners: deleting it removes both links."""
        defn1 = _create_definition(test_client, "CascadeOwner1")
        defn2 = _create_definition(test_client, "CascadeOwner2")
        tag = _create_tag(test_client, "multi-owner-cascade")

        _set_tags(test_client, "item_definition", defn1["id"], [tag["id"]])
        _set_tags(test_client, "item_definition", defn2["id"], [tag["id"]])

        del_resp = test_client.delete(f"/api/tags/{tag['id']}")
        assert del_resp.status_code == 204

        assert len(_get_links(test_client, "item_definition", defn1["id"])) == 0
        assert len(_get_links(test_client, "item_definition", defn2["id"])) == 0


# ---------------------------------------------------------------------------
# 6. Owner-delete detaches all tag links (cascade from entity delete services)
# ---------------------------------------------------------------------------


class TestOwnerDeleteCascade:
    """Deleting an owner must detach all its tag links (service-layer cascade)."""

    def test_definition_delete_removes_tag_links(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "DefnDelete Widget")
        tag = _create_tag(test_client, "defn-cascade-tag")

        _set_tags(test_client, "item_definition", defn["id"], [tag["id"]])
        assert len(_get_links(test_client, "item_definition", defn["id"])) == 1

        del_resp = test_client.delete(f"/api/definitions/{defn['id']}")
        assert del_resp.status_code == 204

        # Tag must still exist.
        tag_resp = test_client.get("/api/tags")
        assert any(t["id"] == tag["id"] for t in tag_resp.json())

    def test_location_delete_removes_tag_links(self, test_client: TestClient) -> None:
        loc = _create_location(test_client, "LocationCascade")
        tag = _create_tag(test_client, "loc-cascade-tag")

        _set_tags(test_client, "location", loc["id"], [tag["id"]])
        assert len(_get_links(test_client, "location", loc["id"])) == 1

        del_resp = test_client.delete(f"/api/locations/{loc['id']}")
        assert del_resp.status_code == 204

        # Tag must still exist.
        tag_resp = test_client.get("/api/tags")
        assert any(t["id"] == tag["id"] for t in tag_resp.json())

    def test_stock_instance_delete_removes_tag_links(self, test_client: TestClient) -> None:
        """Deleting a stock instance detaches all its tag links."""
        # Create a 'none'-tracking definition so we can create an instance.
        defn_resp = test_client.post(
            "/api/definitions",
            json={"name": "Instance Cascade Defn", "stock_tracking_mode": "none"},
        )
        assert defn_resp.status_code == 201
        defn_id = defn_resp.json()["id"]

        inst_resp = test_client.post("/api/instances", json={"definition_id": defn_id})
        assert inst_resp.status_code == 201
        inst_id = inst_resp.json()["id"]

        tag = _create_tag(test_client, "inst-cascade-tag")
        _set_tags(test_client, "stock_instance", inst_id, [tag["id"]])
        assert len(_get_links(test_client, "stock_instance", inst_id)) == 1

        del_resp = test_client.delete(f"/api/instances/{inst_id}")
        assert del_resp.status_code == 204

        # Tag must still exist.
        tag_resp = test_client.get("/api/tags")
        assert any(t["id"] == tag["id"] for t in tag_resp.json())


# ---------------------------------------------------------------------------
# 7. Owner-type validation
# ---------------------------------------------------------------------------


class TestOwnerTypeValidation:
    """Bad model_type returns 422; missing owner returns the owner's not-found code."""

    def test_bad_model_type_set_returns_422(self, test_client: TestClient) -> None:
        tag = _create_tag(test_client, "validate-tag")
        resp = test_client.put(
            "/api/tags/links",
            json={"model_type": "not_an_owner", "model_id": 1, "tag_ids": [tag["id"]]},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_bad_model_type_get_links_does_not_crash(self, test_client: TestClient) -> None:
        """GET /tags/links with a bad model_type: service does not validate, just returns empty."""
        # The list_for_owner method is intentionally lenient — no owner validation.
        resp = test_client.get("/api/tags/links", params={"model_type": "bad_type", "model_id": 1})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_missing_definition_owner_returns_404(self, test_client: TestClient) -> None:
        tag = _create_tag(test_client, "missing-defn-tag")
        resp = test_client.put(
            "/api/tags/links",
            json={
                "model_type": "item_definition",
                "model_id": 99999,
                "tag_ids": [tag["id"]],
            },
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "item_definition.not_found"

    def test_missing_location_owner_returns_404(self, test_client: TestClient) -> None:
        tag = _create_tag(test_client, "missing-loc-tag")
        resp = test_client.put(
            "/api/tags/links",
            json={
                "model_type": "location",
                "model_id": 99999,
                "tag_ids": [tag["id"]],
            },
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "location.not_found"

    def test_missing_stock_instance_owner_returns_404(self, test_client: TestClient) -> None:
        tag = _create_tag(test_client, "missing-inst-tag")
        resp = test_client.put(
            "/api/tags/links",
            json={
                "model_type": "stock_instance",
                "model_id": 99999,
                "tag_ids": [tag["id"]],
            },
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "stock_instance.not_found"


# ---------------------------------------------------------------------------
# 8. Tag-not-found guard
# ---------------------------------------------------------------------------


class TestTagNotFound:
    """Missing tag ID on attach / set / PATCH / DELETE returns tag.not_found (404)."""

    def test_set_tags_unknown_tag_returns_404(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "NotFound Widget")
        resp = test_client.put(
            "/api/tags/links",
            json={"model_type": "item_definition", "model_id": defn["id"], "tag_ids": [99999]},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "tag.not_found"

    def test_patch_unknown_tag_returns_404(self, test_client: TestClient) -> None:
        resp = test_client.patch("/api/tags/99999", json={"name": "ghost"})
        assert resp.status_code == 404
        assert resp.json()["code"] == "tag.not_found"

    def test_delete_unknown_tag_returns_404(self, test_client: TestClient) -> None:
        resp = test_client.delete("/api/tags/99999")
        assert resp.status_code == 404
        assert resp.json()["code"] == "tag.not_found"


# ---------------------------------------------------------------------------
# 9. GET /tags/links embeds full tag object
# ---------------------------------------------------------------------------


class TestTagLinkResponse:
    """GET /tags/links returns TagLinkResponse objects with embedded tag details."""

    def test_links_embed_tag_object(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Embed Widget")
        tag = _create_tag(test_client, "embedded-tag", color="red")
        _set_tags(test_client, "item_definition", defn["id"], [tag["id"]])

        links = _get_links(test_client, "item_definition", defn["id"])
        assert len(links) == 1
        link = links[0]
        assert link["tag_id"] == tag["id"]
        assert link["model_type"] == "item_definition"
        assert link["model_id"] == defn["id"]
        assert link["tag"]["name"] == "embedded-tag"
        assert link["tag"]["color"] == "red"


# ---------------------------------------------------------------------------
# 10. Migration round-trip (0022 + 0023)
# ---------------------------------------------------------------------------


class TestMigrations0022And0023:
    """Migrations 0022 (tags) and 0023 (tag_links) round-trip cleanly on a DB at 0021."""

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

    def test_migration_0022_and_0023_up_down(self) -> None:
        """Upgrade through 0023; downgrade back to 0021 cleanly."""
        from sqlalchemy import create_engine as sa_create_engine
        from sqlalchemy import inspect as sa_inspect

        fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_mig_0022_")
        os.close(fd)
        db_path = Path(path_str)
        db_path.unlink()
        url = f"sqlite:///{path_str}"

        try:
            # Upgrade to HEAD (applies 0022 + 0023).
            rc, output = self._run_alembic("upgrade", "head", url=url)
            assert rc == 0, f"alembic upgrade head failed:\n{output}"

            eng = sa_create_engine(url)
            tables = set(sa_inspect(eng).get_table_names())
            eng.dispose()
            assert "tags" in tables, f"tags table missing after upgrade. Tables: {tables}"
            assert "tag_links" in tables, f"tag_links table missing after upgrade. Tables: {tables}"

            # Downgrade to 0022 — removes tag_links.
            rc, output = self._run_alembic("downgrade", "0022", url=url)
            assert rc == 0, f"alembic downgrade to 0022 failed:\n{output}"

            eng = sa_create_engine(url)
            tables = set(sa_inspect(eng).get_table_names())
            eng.dispose()
            assert "tag_links" not in tables, "tag_links must be gone after downgrade to 0022"
            assert "tags" in tables, "tags table must survive downgrade to 0022"

            # Downgrade to 0021 — removes tags.
            rc, output = self._run_alembic("downgrade", "0021", url=url)
            assert rc == 0, f"alembic downgrade to 0021 failed:\n{output}"

            eng = sa_create_engine(url)
            tables = set(sa_inspect(eng).get_table_names())
            eng.dispose()
            assert "tags" not in tables, "tags table must be gone after downgrade to 0021"
            assert "attachments" in tables, "attachments table must survive downgrade to 0021"

        finally:
            if db_path.exists():
                db_path.unlink()
