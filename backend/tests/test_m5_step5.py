"""Tests for M5 Step 5: barcodes and product-lookup provider seam.

Coverage
--------
- Binding a code already bound to the **same** definition → ``barcode.duplicate`` (409).
- Binding a code already bound to **another** definition → ``barcode.duplicate`` (409).
- Binding to a non-existent definition → 404 (``item_definition.not_found``).
- Definition delete (no instances) cascades its barcodes via FK CASCADE (PRAGMA on).
- ``GET /barcodes/lookup`` returns the internal match for a known code
  (``found=true``, ``source="internal"``, correct ``definition_id``/``name``).
- ``GET /barcodes/lookup`` returns ``found=false`` + HTTP 200 for an unknown code.
- ``DELETE /barcodes/{id}`` removes the code; a second DELETE → 404.
- ``GET /definitions/{id}/barcodes`` returns all codes bound to a definition.
- Provider chain unit-test: ``ProductLookupService`` with fake providers verifies
  first-hit semantics, iteration order, and that the Protocol seam works.
- Migration 0027 upgrade + downgrade cleanly on a DB at revision 0026.
"""

from __future__ import annotations

import importlib
import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m5_step5_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m5-step5")
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
    """TestClient with full schema (all models including Barcode), authenticated admin."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.attachment as attachment_mod
    import app.models.audit_log as audit_log_mod
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

    # Reload all model modules so the new Barcode model registers on the fresh Base.
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


def _create_definition(client: TestClient, name: str) -> dict:  # type: ignore[type-arg]
    resp = client.post(
        "/api/definitions",
        json={"name": name, "stock_tracking_mode": "none"},
    )
    assert resp.status_code == 201, f"create_definition failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _bind_barcode(
    client: TestClient,
    definition_id: int,
    code: str,
    *,
    symbology: str = "ean13",
    label: str | None = None,
) -> dict:  # type: ignore[type-arg]
    body: dict[str, object] = {"code": code, "symbology": symbology}  # type: ignore[type-arg]
    if label is not None:
        body["label"] = label
    resp = client.post(f"/api/definitions/{definition_id}/barcodes", json=body)
    assert resp.status_code == 201, f"bind_barcode failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. Bind: happy path + field persistence
# ---------------------------------------------------------------------------


class TestBind:
    """Barcode bind and list tests."""

    def test_bind_returns_201_with_correct_fields(self, test_client: TestClient) -> None:
        """POST /definitions/{id}/barcodes → 201 with expected fields."""
        defn = _create_definition(test_client, "Test Item")
        resp = test_client.post(
            f"/api/definitions/{defn['id']}/barcodes",
            json={"code": "1234567890128", "symbology": "ean13", "label": "single"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["definition_id"] == defn["id"]
        assert data["code"] == "1234567890128"
        assert data["symbology"] == "ean13"
        assert data["label"] == "single"
        assert "id" in data
        assert "created_at" in data

    def test_bind_default_symbology_unknown(self, test_client: TestClient) -> None:
        """Omitting symbology → defaults to 'unknown'."""
        defn = _create_definition(test_client, "Item B")
        resp = test_client.post(
            f"/api/definitions/{defn['id']}/barcodes",
            json={"code": "ABC-001"},
        )
        assert resp.status_code == 201
        assert resp.json()["symbology"] == "unknown"

    def test_list_barcodes_for_definition(self, test_client: TestClient) -> None:
        """GET /definitions/{id}/barcodes returns all bound codes."""
        defn = _create_definition(test_client, "Multi-Code Item")
        _bind_barcode(test_client, defn["id"], "CODE-A")
        _bind_barcode(test_client, defn["id"], "CODE-B")
        resp = test_client.get(f"/api/definitions/{defn['id']}/barcodes")
        assert resp.status_code == 200
        codes = [b["code"] for b in resp.json()]
        assert "CODE-A" in codes
        assert "CODE-B" in codes
        assert len(codes) == 2

    def test_list_barcodes_empty_for_no_codes(self, test_client: TestClient) -> None:
        """GET /definitions/{id}/barcodes returns [] when no codes are bound."""
        defn = _create_definition(test_client, "Empty Item")
        resp = test_client.get(f"/api/definitions/{defn['id']}/barcodes")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# 2. Duplicate code enforcement
# ---------------------------------------------------------------------------


class TestDuplicate:
    """barcode.duplicate (409) is raised for any duplicate code, same or different definition."""

    def test_same_definition_duplicate_returns_409(self, test_client: TestClient) -> None:
        """Binding the same code to the SAME definition a second time → 409."""
        defn = _create_definition(test_client, "Item Dup Same")
        _bind_barcode(test_client, defn["id"], "DUP-CODE-1")
        resp = test_client.post(
            f"/api/definitions/{defn['id']}/barcodes",
            json={"code": "DUP-CODE-1"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "barcode.duplicate"

    def test_different_definition_duplicate_returns_409(self, test_client: TestClient) -> None:
        """Binding the same code to a DIFFERENT definition → 409."""
        defn_a = _create_definition(test_client, "Item A")
        defn_b = _create_definition(test_client, "Item B")
        _bind_barcode(test_client, defn_a["id"], "DUP-CODE-2")
        resp = test_client.post(
            f"/api/definitions/{defn_b['id']}/barcodes",
            json={"code": "DUP-CODE-2"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "barcode.duplicate"

    def test_different_code_on_same_definition_is_ok(self, test_client: TestClient) -> None:
        """A different code on the same definition is allowed."""
        defn = _create_definition(test_client, "Multi Code Item")
        _bind_barcode(test_client, defn["id"], "CODE-X1")
        resp = test_client.post(
            f"/api/definitions/{defn['id']}/barcodes",
            json={"code": "CODE-X2"},
        )
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# 3. Non-existent definition → 404
# ---------------------------------------------------------------------------


class TestDefinitionNotFound:
    """Binding to a non-existent definition → 404."""

    def test_bind_nonexistent_definition_returns_404(self, test_client: TestClient) -> None:
        resp = test_client.post(
            "/api/definitions/99999/barcodes",
            json={"code": "GHOST-CODE"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "item_definition.not_found"


# ---------------------------------------------------------------------------
# 4. Unbind
# ---------------------------------------------------------------------------


class TestUnbind:
    """DELETE /barcodes/{id} tests."""

    def test_unbind_removes_barcode(self, test_client: TestClient) -> None:
        """DELETE removes the barcode; list is empty afterwards."""
        defn = _create_definition(test_client, "Unbind Item")
        barcode = _bind_barcode(test_client, defn["id"], "REMOVE-ME")
        resp = test_client.delete(f"/api/barcodes/{barcode['id']}")
        assert resp.status_code == 204
        # The code should no longer appear in the definition's list.
        list_resp = test_client.get(f"/api/definitions/{defn['id']}/barcodes")
        assert list_resp.status_code == 200
        assert list_resp.json() == []

    def test_double_unbind_returns_404(self, test_client: TestClient) -> None:
        """Deleting an already-removed barcode → 404 (barcode.not_found)."""
        defn = _create_definition(test_client, "Double Unbind")
        barcode = _bind_barcode(test_client, defn["id"], "DEL-TWICE")
        test_client.delete(f"/api/barcodes/{barcode['id']}")
        resp = test_client.delete(f"/api/barcodes/{barcode['id']}")
        assert resp.status_code == 404
        assert resp.json()["code"] == "barcode.not_found"

    def test_unbind_nonexistent_returns_404(self, test_client: TestClient) -> None:
        """DELETE /barcodes/999999 → 404."""
        resp = test_client.delete("/api/barcodes/999999")
        assert resp.status_code == 404
        assert resp.json()["code"] == "barcode.not_found"


# ---------------------------------------------------------------------------
# 5. Definition delete cascades barcodes (FK CASCADE)
# ---------------------------------------------------------------------------


class TestDefinitionDeleteCascade:
    """Deleting a definition cascades (DB-level FK CASCADE) its barcodes."""

    def test_definition_delete_cascades_barcodes(self, test_client: TestClient) -> None:
        """Deleting a definition removes its barcodes (FK ondelete=CASCADE)."""
        defn = _create_definition(test_client, "To Be Deleted")
        bc = _bind_barcode(test_client, defn["id"], "WILL-CASCADE")

        # Delete the definition (no stock instances → allowed).
        del_resp = test_client.delete(f"/api/definitions/{defn['id']}")
        assert del_resp.status_code == 204

        # The barcode row should be gone (FK CASCADE).
        # Verify via unbind: should get 404, not a DB integrity error.
        resp = test_client.delete(f"/api/barcodes/{bc['id']}")
        assert resp.status_code == 404
        assert resp.json()["code"] == "barcode.not_found"

    def test_cascaded_code_can_be_rebound(self, test_client: TestClient) -> None:
        """After a cascade, the same code can be bound to a new definition."""
        defn_a = _create_definition(test_client, "Cascade Source")
        _bind_barcode(test_client, defn_a["id"], "REBIND-CODE")
        test_client.delete(f"/api/definitions/{defn_a['id']}")

        defn_b = _create_definition(test_client, "New Owner")
        resp = test_client.post(
            f"/api/definitions/{defn_b['id']}/barcodes",
            json={"code": "REBIND-CODE"},
        )
        assert resp.status_code == 201
        assert resp.json()["definition_id"] == defn_b["id"]


# ---------------------------------------------------------------------------
# 6. Lookup endpoint
# ---------------------------------------------------------------------------


class TestLookup:
    """GET /barcodes/lookup tests."""

    def test_lookup_known_code_returns_found_true(self, test_client: TestClient) -> None:
        """Lookup of a bound code → found=true, source="internal", correct definition."""
        defn = _create_definition(test_client, "Lookup Target")
        _bind_barcode(test_client, defn["id"], "LOOKUP-HIT")

        resp = test_client.get("/api/barcodes/lookup?code=LOOKUP-HIT")
        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is True
        assert data["source"] == "internal"
        assert data["definition"] is not None
        assert data["definition"]["id"] == defn["id"]
        assert data["definition"]["name"] == "Lookup Target"
        assert data["draft"] is None

    def test_lookup_unknown_code_returns_found_false_200(self, test_client: TestClient) -> None:
        """Lookup of an unbound code → 200 with found=false and all nulls."""
        resp = test_client.get("/api/barcodes/lookup?code=NO-SUCH-CODE")
        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is False
        assert data["source"] is None
        assert data["definition"] is None
        assert data["draft"] is None

    def test_lookup_after_unbind_returns_found_false(self, test_client: TestClient) -> None:
        """After unbinding, lookup returns found=false (not a stale hit)."""
        defn = _create_definition(test_client, "Temp Item")
        bc = _bind_barcode(test_client, defn["id"], "TEMP-CODE")
        test_client.delete(f"/api/barcodes/{bc['id']}")

        resp = test_client.get("/api/barcodes/lookup?code=TEMP-CODE")
        assert resp.status_code == 200
        assert resp.json()["found"] is False

    def test_lookup_after_definition_cascade_returns_found_false(
        self, test_client: TestClient
    ) -> None:
        """After definition delete (cascade), lookup for the old code → found=false."""
        defn = _create_definition(test_client, "Cascaded Defn")
        _bind_barcode(test_client, defn["id"], "CASCADE-LOOKUP")
        test_client.delete(f"/api/definitions/{defn['id']}")

        resp = test_client.get("/api/barcodes/lookup?code=CASCADE-LOOKUP")
        assert resp.status_code == 200
        assert resp.json()["found"] is False

    def test_lookup_missing_code_param_returns_422(self, test_client: TestClient) -> None:
        """Missing required ?code= query parameter → 422."""
        resp = test_client.get("/api/barcodes/lookup")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 7. Provider chain unit tests (no HTTP, no DB)
# ---------------------------------------------------------------------------


class TestProviderChain:
    """Unit tests for ProductLookupService provider iteration semantics."""

    def _miss_provider(self) -> object:
        """A fake provider that always returns None (miss)."""
        from app.services.product_lookup.provider import ProductLookupProvider

        class MissProvider:
            def lookup(self, code: str) -> None:
                return None

        assert isinstance(MissProvider(), ProductLookupProvider), (
            "MissProvider must satisfy the ProductLookupProvider Protocol"
        )
        return MissProvider()

    def _hit_provider(self, source: str) -> object:
        """A fake provider that always returns a hit with the given source."""
        from app.services.product_lookup.provider import ProductLookupProvider, ProductLookupResult

        class HitProvider:
            def __init__(self, src: str) -> None:
                self._src = src

            def lookup(self, code: str) -> ProductLookupResult:
                return ProductLookupResult(source=self._src)

        assert isinstance(HitProvider(source), ProductLookupProvider), (
            "HitProvider must satisfy the ProductLookupProvider Protocol"
        )
        return HitProvider(source)

    def test_empty_provider_list_returns_none(self) -> None:
        """No providers → lookup returns None."""
        from app.services.product_lookup.service import ProductLookupService

        svc = ProductLookupService([])
        assert svc.lookup("any-code") is None

    def test_single_miss_returns_none(self) -> None:
        """A single miss provider → None."""
        from app.services.product_lookup.service import ProductLookupService

        svc = ProductLookupService([self._miss_provider()])  # type: ignore[list-item]
        assert svc.lookup("any-code") is None

    def test_single_hit_returns_result(self) -> None:
        """A single hit provider → that result is returned."""
        from app.services.product_lookup.service import ProductLookupService

        svc = ProductLookupService([self._hit_provider("test_source")])  # type: ignore[list-item]
        result = svc.lookup("any-code")
        assert result is not None
        assert result.source == "test_source"

    def test_first_hit_wins_chain_stops(self) -> None:
        """When the first provider hits, the second is never called."""
        from app.services.product_lookup.service import ProductLookupService

        called: list[str] = []

        from app.services.product_lookup.provider import ProductLookupResult

        class TrackingProvider:
            def __init__(self, name: str, hits: bool) -> None:
                self._name = name
                self._hits = hits

            def lookup(self, code: str) -> ProductLookupResult | None:
                called.append(self._name)
                if self._hits:
                    return ProductLookupResult(source=self._name)
                return None

        p1 = TrackingProvider("first", hits=True)
        p2 = TrackingProvider("second", hits=True)
        svc = ProductLookupService([p1, p2])  # type: ignore[list-item]
        result = svc.lookup("x")

        assert result is not None
        assert result.source == "first"
        assert called == ["first"], "second provider must NOT be called after first hit"

    def test_miss_then_hit_returns_second(self) -> None:
        """First provider misses, second provider hits → second result returned."""
        from app.services.product_lookup.service import ProductLookupService

        svc = ProductLookupService(
            [  # type: ignore[list-item]
                self._miss_provider(),
                self._hit_provider("second_source"),
            ]
        )
        result = svc.lookup("x")
        assert result is not None
        assert result.source == "second_source"

    def test_all_miss_returns_none(self) -> None:
        """All providers miss → None."""
        from app.services.product_lookup.service import ProductLookupService

        svc = ProductLookupService(
            [  # type: ignore[list-item]
                self._miss_provider(),
                self._miss_provider(),
            ]
        )
        assert svc.lookup("x") is None

    def test_protocol_seam_isinstance_check(self) -> None:
        """runtime_checkable Protocol: isinstance check works for compliant classes."""
        from app.services.product_lookup.provider import ProductLookupProvider

        class GoodProvider:
            def lookup(self, code: str) -> None:
                return None

        class BadProvider:
            pass  # No lookup method.

        assert isinstance(GoodProvider(), ProductLookupProvider)
        assert not isinstance(BadProvider(), ProductLookupProvider)


# ---------------------------------------------------------------------------
# 8. Migration round-trip: 0027 upgrade + downgrade
# ---------------------------------------------------------------------------


class TestMigrationRoundTrip:
    """Migration 0027 (barcodes) upgrades and downgrades cleanly."""

    def _run_alembic(self, *args: str, url: str) -> tuple[int, str]:
        """Run an alembic command as a subprocess.

        Uses subprocess (not ``alembic.command``) to avoid the local
        ``backend/alembic/`` package directory shadowing the installed
        ``alembic`` distribution — the same pattern used in ``test_step3.py``.
        """
        import subprocess

        backend_root = Path(__file__).parent.parent
        env = {
            **os.environ,
            "SECRET_KEY": "test-secret-key-migration",
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

    def test_upgrade_and_downgrade_0027(
        self,
        temp_db: Path,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ARG002
    ) -> None:
        """Migration 0027: upgrade creates 'barcodes'; downgrade drops it."""
        import sqlalchemy as sa

        url = f"sqlite:///{temp_db}"

        # Upgrade through 0027.
        rc, output = self._run_alembic("upgrade", "0027", url=url)
        assert rc == 0, f"alembic upgrade to 0027 failed:\n{output}"

        engine = sa.create_engine(url)
        inspector = sa.inspect(engine)
        assert "barcodes" in inspector.get_table_names(), (
            "barcodes table must exist after upgrade to 0027"
        )
        cols = {c["name"] for c in inspector.get_columns("barcodes")}
        assert cols >= {"id", "definition_id", "code", "symbology", "label", "created_at"}
        engine.dispose()

        # Downgrade back to 0026.
        rc, output = self._run_alembic("downgrade", "0026", url=url)
        assert rc == 0, f"alembic downgrade to 0026 failed:\n{output}"

        engine = sa.create_engine(url)
        inspector = sa.inspect(engine)
        assert "barcodes" not in inspector.get_table_names(), (
            "barcodes table must be dropped after downgrade to 0026"
        )
        engine.dispose()
