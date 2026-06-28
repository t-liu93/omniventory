"""Tests for container_asset_label on LocationResponse and LocationTreeNode.

Verifies that:
- A location linked as a container-as-item returns a human-readable
  ``container_asset_label`` (definition name + optional serial) in:
    * GET /locations            (flat list)
    * GET /locations/{id}       (single)
    * PATCH /locations/{id}     (after link/update)
    * GET /locations/tree       (tree nodes)
- An unlinked (normal) location has ``container_asset_label`` = None in all
  the same endpoints.
- Label format: ``"<def name>"`` when serial is absent,
  ``"<def name> · SN <serial>"`` when serial is present.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from tests.conftest import drop_all_sqlite
from tests.test_m1_step4 import (
    _create_definition,
    _create_instance,
    _create_location,
    _make_temp_db_url,
)

# ---------------------------------------------------------------------------
# Fixtures (same pattern as test_m1_step4)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_caches() -> Generator[None]:
    from app.config import get_settings
    from app.db.base import get_engine

    get_settings.cache_clear()
    get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-container-label")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def client(temp_db: Path) -> Generator[TestClient]:  # noqa: ARG001
    """Authenticated TestClient with full schema."""
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
    importlib.reload(audit_log_mod)

    from app.db.base import Base, get_engine
    from app.main import create_app

    engine = get_engine()
    Base.metadata.create_all(engine)
    app = create_app()

    with TestClient(app, raise_server_exceptions=True) as tc:
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            from app.auth.passwords import hash_password
            from app.models.item_kind import ItemKind
            from app.repositories.user import UserRepository

            repo = UserRepository(db)
            repo.create(email="admin@example.com", password_hash=hash_password("pw"))
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

        resp = tc.post("/api/auth/login", json={"email": "admin@example.com", "password": "pw"})
        assert resp.status_code == 200
        yield tc

    drop_all_sqlite(Base, engine)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _link_container(tc: TestClient, location_id: int, instance_id: int) -> dict:  # type: ignore[type-arg]
    resp = tc.patch(f"/api/locations/{location_id}", json={"item_instance_id": instance_id})
    assert resp.status_code == 200, resp.json()
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestContainerAssetLabel:
    """``container_asset_label`` is populated correctly across all location endpoints."""

    def test_unlinked_location_label_is_none_flat_list(self, client: TestClient) -> None:
        """Normal (unlinked) location has container_asset_label=None in GET /locations."""
        loc = _create_location(client, "Garage")
        resp = client.get("/api/locations")
        assert resp.status_code == 200
        locs_by_id = {row["id"]: row for row in resp.json()}
        assert locs_by_id[loc["id"]]["container_asset_label"] is None

    def test_unlinked_location_label_is_none_single(self, client: TestClient) -> None:
        """Normal (unlinked) location has container_asset_label=None in GET /locations/{id}."""
        loc = _create_location(client, "Shelf")
        resp = client.get(f"/api/locations/{loc['id']}")
        assert resp.status_code == 200
        assert resp.json()["container_asset_label"] is None

    def test_unlinked_location_label_is_none_tree(self, client: TestClient) -> None:
        """Normal (unlinked) location has container_asset_label=None in GET /locations/tree."""
        _create_location(client, "Kitchen")
        resp = client.get("/api/locations/tree")
        assert resp.status_code == 200

        def _find(nodes: list, name: str) -> dict | None:  # type: ignore[type-arg]
            for n in nodes:
                if n["name"] == name:
                    return n  # type: ignore[return-value]
                found = _find(n.get("children", []), name)
                if found:
                    return found
            return None

        node = _find(resp.json(), "Kitchen")
        assert node is not None
        assert node["container_asset_label"] is None

    def test_linked_location_label_no_serial(self, client: TestClient) -> None:
        """Container location without serial shows only definition name."""
        loc = _create_location(client, "Toolbox")
        defn = _create_definition(client, "Lboxx-136")
        inst = _create_instance(client, defn["id"])  # no serial

        _link_container(client, loc["id"], inst["id"])

        # GET /locations/{id}
        resp = client.get(f"/api/locations/{loc['id']}")
        assert resp.status_code == 200
        assert resp.json()["container_asset_label"] == "Lboxx-136"

    def test_linked_location_label_with_serial(self, client: TestClient) -> None:
        """Container location with serial shows definition name and serial."""
        loc = _create_location(client, "Toolbox")
        defn = _create_definition(client, "Lboxx-136")
        inst = _create_instance(client, defn["id"], serial="SN-99")

        _link_container(client, loc["id"], inst["id"])

        resp = client.get(f"/api/locations/{loc['id']}")
        assert resp.status_code == 200
        assert resp.json()["container_asset_label"] == "Lboxx-136 · SN SN-99"

    def test_linked_location_label_in_flat_list(self, client: TestClient) -> None:
        """container_asset_label is populated in GET /locations flat list."""
        normal_loc = _create_location(client, "Shelf")
        container_loc = _create_location(client, "Drawer")
        defn = _create_definition(client, "Stanley Box")
        inst = _create_instance(client, defn["id"], serial="TB-01")
        _link_container(client, container_loc["id"], inst["id"])

        resp = client.get("/api/locations")
        assert resp.status_code == 200
        by_id = {row["id"]: row for row in resp.json()}

        # Linked location has the label.
        assert by_id[container_loc["id"]]["container_asset_label"] == "Stanley Box · SN TB-01"
        # Normal location has None.
        assert by_id[normal_loc["id"]]["container_asset_label"] is None

    def test_linked_location_label_in_tree(self, client: TestClient) -> None:
        """container_asset_label is populated in GET /locations/tree."""
        container_loc = _create_location(client, "ContainerBox")
        defn = _create_definition(client, "ToughCase")
        inst = _create_instance(client, defn["id"])  # no serial
        _link_container(client, container_loc["id"], inst["id"])

        resp = client.get("/api/locations/tree")
        assert resp.status_code == 200

        def _find(nodes: list, name: str) -> dict | None:  # type: ignore[type-arg]
            for n in nodes:
                if n["name"] == name:
                    return n  # type: ignore[return-value]
                found = _find(n.get("children", []), name)
                if found:
                    return found
            return None

        node = _find(resp.json(), "ContainerBox")
        assert node is not None
        assert node["container_asset_label"] == "ToughCase"

    def test_label_cleared_after_unlink(self, client: TestClient) -> None:
        """container_asset_label returns None after the container link is removed."""
        loc = _create_location(client, "Crate")
        defn = _create_definition(client, "BigBox")
        inst = _create_instance(client, defn["id"])
        _link_container(client, loc["id"], inst["id"])

        # Verify label is set.
        resp = client.get(f"/api/locations/{loc['id']}")
        assert resp.json()["container_asset_label"] == "BigBox"

        # Unlink.
        resp = client.patch(f"/api/locations/{loc['id']}", json={"item_instance_id": None})
        assert resp.status_code == 200
        assert resp.json()["container_asset_label"] is None

    def test_patch_response_includes_label(self, client: TestClient) -> None:
        """PATCH /locations/{id} response already contains container_asset_label."""
        loc = _create_location(client, "Box")
        defn = _create_definition(client, "MetalBox")
        inst = _create_instance(client, defn["id"], serial="MB-1")

        resp = client.patch(f"/api/locations/{loc['id']}", json={"item_instance_id": inst["id"]})
        assert resp.status_code == 200
        assert resp.json()["container_asset_label"] == "MetalBox · SN MB-1"
