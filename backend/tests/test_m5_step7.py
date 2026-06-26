"""Tests for M5 Step 7: CSV / JSON data export.

Coverage
--------
- CSV header row matches the expected column list for each of the three
  entities (item_definitions, stock_instances, locations).
- CSV data rows contain correct values (FK id + resolved name, tags,
  custom_fields, Decimal/date formatting).
- FK name resolution: ``*_name`` columns carry resolved names; a NULL FK
  yields an empty name cell in CSV and ``null`` in JSON.
- Tags comma-joined column is populated from tag_links.
- ``custom_fields`` column is serialized as a JSON string.
- CSV escaping correctness: values containing a comma, a double-quote, and a
  newline are preserved exactly when the CSV is round-tripped through
  ``csv.reader``.
- JSON shape matches CSV columns (same keys and semantically equivalent values).
- Bad entity → 422 ``validation.invalid_input``.
- Bad format → 422 ``validation.invalid_input``.
- Unauthenticated → 401.
- Streamed: ``ExportService.export()`` returns a generator (not a fully-
  materialised list).
- Decimal and date formatting is stable (locale-independent exact strings).
"""

from __future__ import annotations

import csv
import importlib
import inspect
import io
import json
import os
import tempfile
from collections.abc import Generator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Fixture infrastructure (mirrors test_m5_step5 / test_m5_step6 pattern)
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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m5_step7_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m5-step7")
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
    """TestClient with full schema (all M5 models), authenticated admin."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

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


def _create_category(client: TestClient, name: str) -> dict:  # type: ignore[type-arg]
    resp = client.post("/api/categories", json={"name": name})
    assert resp.status_code == 201, f"create_category failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_location(
    client: TestClient,
    name: str,
    *,
    parent_id: int | None = None,
    description: str | None = None,
) -> dict:  # type: ignore[type-arg]
    payload: dict[str, object] = {"name": name}  # type: ignore[type-arg]
    if parent_id is not None:
        payload["parent_id"] = parent_id
    if description is not None:
        payload["description"] = description
    resp = client.post("/api/locations", json=payload)
    assert resp.status_code == 201, f"create_location failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_definition(
    client: TestClient,
    name: str,
    *,
    category_id: int | None = None,
    default_location_id: int | None = None,
    custom_fields: dict | None = None,  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    payload: dict[str, object] = {"name": name, "stock_tracking_mode": "none"}  # type: ignore[type-arg]
    if category_id is not None:
        payload["category_id"] = category_id
    if default_location_id is not None:
        payload["default_location_id"] = default_location_id
    if custom_fields is not None:
        payload["custom_fields"] = custom_fields
    resp = client.post("/api/definitions", json=payload)
    assert resp.status_code == 201, f"create_definition failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_instance(
    client: TestClient,
    definition_id: int,
    *,
    location_id: int | None = None,
    serial: str | None = None,
    purchase_price: str | None = None,
    best_before_date: str | None = None,
    custom_fields: dict | None = None,  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    payload: dict[str, object] = {"definition_id": definition_id}  # type: ignore[type-arg]
    if location_id is not None:
        payload["location_id"] = location_id
    if serial is not None:
        payload["serial"] = serial
    if purchase_price is not None:
        payload["purchase_price"] = purchase_price
    if best_before_date is not None:
        payload["best_before_date"] = best_before_date
    if custom_fields is not None:
        payload["custom_fields"] = custom_fields
    resp = client.post("/api/instances", json=payload)
    assert resp.status_code == 201, f"create_instance failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _create_tag(client: TestClient, name: str) -> dict:  # type: ignore[type-arg]
    resp = client.post("/api/tags", json={"name": name})
    assert resp.status_code == 201, f"create_tag failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _set_tags(
    client: TestClient,
    model_type: str,
    model_id: int,
    tag_ids: list[int],
) -> None:
    resp = client.put(
        "/api/tags/links",
        json={"model_type": model_type, "model_id": model_id, "tag_ids": tag_ids},
    )
    assert resp.status_code == 200, f"set_tags failed: {resp.json()}"


def _parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    """Parse a CSV string into (header, data_rows)."""
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    # csv.writer appends a trailing newline; the last row may be empty
    rows = [r for r in rows if r]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _export(
    client: TestClient,
    entity: str,
    fmt: str = "csv",
) -> tuple[int, str]:
    """Call GET /export/{entity}?format={fmt}; return (status_code, body_text)."""
    resp = client.get(f"/api/export/{entity}", params={"format": fmt})
    return resp.status_code, resp.text


# ---------------------------------------------------------------------------
# 1. CSV header — correct column list per entity
# ---------------------------------------------------------------------------


class TestCSVHeaders:
    """CSV header row matches the expected column list for each entity."""

    def test_item_definitions_header(self, test_client: TestClient) -> None:
        from app.services.export import ITEM_DEFINITION_COLUMNS

        _create_definition(test_client, "Widget")
        status, body = _export(test_client, "item_definitions", "csv")
        assert status == 200
        header, _ = _parse_csv(body)
        assert header == ITEM_DEFINITION_COLUMNS

    def test_stock_instances_header(self, test_client: TestClient) -> None:
        from app.services.export import STOCK_INSTANCE_COLUMNS

        defn = _create_definition(test_client, "Gadget")
        _create_instance(test_client, defn["id"])
        status, body = _export(test_client, "stock_instances", "csv")
        assert status == 200
        header, _ = _parse_csv(body)
        assert header == STOCK_INSTANCE_COLUMNS

    def test_locations_header(self, test_client: TestClient) -> None:
        from app.services.export import LOCATION_COLUMNS

        _create_location(test_client, "Kitchen")
        status, body = _export(test_client, "locations", "csv")
        assert status == 200
        header, _ = _parse_csv(body)
        assert header == LOCATION_COLUMNS

    def test_empty_export_has_header_only(self, test_client: TestClient) -> None:
        """An empty DB still exports a header-only CSV (no data rows)."""
        from app.services.export import LOCATION_COLUMNS

        status, body = _export(test_client, "locations", "csv")
        assert status == 200
        header, rows = _parse_csv(body)
        assert header == LOCATION_COLUMNS
        assert rows == []


# ---------------------------------------------------------------------------
# 2. CSV data rows — correct values
# ---------------------------------------------------------------------------


class TestCSVDataRows:
    """Data rows carry correct field values for each entity."""

    def test_item_definition_row_values(self, test_client: TestClient) -> None:
        cat = _create_category(test_client, "Electronics")
        loc = _create_location(test_client, "Shelf A")
        defn = _create_definition(
            test_client,
            "Laptop",
            category_id=cat["id"],
            default_location_id=loc["id"],
        )

        status, body = _export(test_client, "item_definitions", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        assert len(rows) == 1
        row = rows[0]

        from app.services.export import ITEM_DEFINITION_COLUMNS

        d = dict(zip(ITEM_DEFINITION_COLUMNS, row, strict=False))
        assert d["id"] == str(defn["id"])
        assert d["name"] == "Laptop"
        assert d["category_id"] == str(cat["id"])
        assert d["category_name"] == "Electronics"
        assert d["default_location_id"] == str(loc["id"])
        assert d["default_location_name"] == "Shelf A"

    def test_stock_instance_row_values(self, test_client: TestClient) -> None:
        loc = _create_location(test_client, "Garage")
        defn = _create_definition(test_client, "Drill")
        inst = _create_instance(
            test_client,
            defn["id"],
            location_id=loc["id"],
            serial="SN-12345",
        )

        status, body = _export(test_client, "stock_instances", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        assert len(rows) == 1
        row = rows[0]

        from app.services.export import STOCK_INSTANCE_COLUMNS

        d = dict(zip(STOCK_INSTANCE_COLUMNS, row, strict=False))
        assert d["id"] == str(inst["id"])
        assert d["definition_id"] == str(defn["id"])
        assert d["definition_name"] == "Drill"
        assert d["location_id"] == str(loc["id"])
        assert d["location_name"] == "Garage"
        assert d["serial"] == "SN-12345"

    def test_location_row_values(self, test_client: TestClient) -> None:
        parent = _create_location(test_client, "House")
        child = _create_location(test_client, "Bedroom", parent_id=parent["id"])

        status, body = _export(test_client, "locations", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        assert len(rows) == 2

        from app.services.export import LOCATION_COLUMNS

        rows_by_id = {
            r[LOCATION_COLUMNS.index("id")]: dict(zip(LOCATION_COLUMNS, r, strict=False))
            for r in rows
        }
        child_row = rows_by_id[str(child["id"])]
        assert child_row["parent_id"] == str(parent["id"])
        assert child_row["parent_name"] == "House"

        parent_row = rows_by_id[str(parent["id"])]
        assert parent_row["parent_id"] == ""  # NULL FK → empty CSV cell
        assert parent_row["parent_name"] == ""  # NULL FK → empty CSV cell


# ---------------------------------------------------------------------------
# 3. FK name resolution — NULL FK yields empty name
# ---------------------------------------------------------------------------


class TestFKResolution:
    """NULL FKs yield empty CSV cells and null in JSON."""

    def test_definition_no_category_or_location(self, test_client: TestClient) -> None:
        """A definition with NULL category_id and default_location_id → empty names."""
        _create_definition(test_client, "No-Category Item")

        status, body = _export(test_client, "item_definitions", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        from app.services.export import ITEM_DEFINITION_COLUMNS

        row = rows[0]
        d = dict(zip(ITEM_DEFINITION_COLUMNS, row, strict=False))
        assert d["category_id"] == ""
        assert d["category_name"] == ""
        assert d["default_location_id"] == ""
        assert d["default_location_name"] == ""

    def test_instance_no_location(self, test_client: TestClient) -> None:
        """A stock instance with NULL location_id → empty location_name cell."""
        defn = _create_definition(test_client, "No-Location Item")
        _create_instance(test_client, defn["id"])

        status, body = _export(test_client, "stock_instances", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        from app.services.export import STOCK_INSTANCE_COLUMNS

        d = dict(zip(STOCK_INSTANCE_COLUMNS, rows[0], strict=False))
        assert d["location_id"] == ""
        assert d["location_name"] == ""

    def test_json_null_fk_is_json_null(self, test_client: TestClient) -> None:
        """NULL FK in JSON export → JSON null (not empty string)."""
        _create_definition(test_client, "NullFK")
        status, body = _export(test_client, "item_definitions", "json")
        assert status == 200
        records = json.loads(body)
        assert len(records) == 1
        rec = records[0]
        assert rec["category_id"] is None
        assert rec["category_name"] is None
        assert rec["default_location_id"] is None
        assert rec["default_location_name"] is None


# ---------------------------------------------------------------------------
# 4. Tags column
# ---------------------------------------------------------------------------


class TestTagsColumn:
    """Tags are comma-joined into the ``tags`` CSV column."""

    def test_single_tag_on_definition(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Tagged Item")
        tag = _create_tag(test_client, "electronics")
        _set_tags(test_client, "item_definition", defn["id"], [tag["id"]])

        status, body = _export(test_client, "item_definitions", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        from app.services.export import ITEM_DEFINITION_COLUMNS

        d = dict(zip(ITEM_DEFINITION_COLUMNS, rows[0], strict=False))
        assert d["tags"] == "electronics"

    def test_multiple_tags_comma_joined(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Multi-Tag")
        tag_a = _create_tag(test_client, "fragile")
        tag_b = _create_tag(test_client, "expensive")
        # Attach both tags (IDs in ascending order for stable ordering).
        _set_tags(
            test_client,
            "item_definition",
            defn["id"],
            sorted([tag_a["id"], tag_b["id"]]),
        )

        status, body = _export(test_client, "item_definitions", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        from app.services.export import ITEM_DEFINITION_COLUMNS

        d = dict(zip(ITEM_DEFINITION_COLUMNS, rows[0], strict=False))
        tags = d["tags"].split(",")
        assert set(tags) == {"fragile", "expensive"}

    def test_no_tags_yields_empty_string(self, test_client: TestClient) -> None:
        _create_definition(test_client, "No Tags")

        status, body = _export(test_client, "item_definitions", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        from app.services.export import ITEM_DEFINITION_COLUMNS

        d = dict(zip(ITEM_DEFINITION_COLUMNS, rows[0], strict=False))
        assert d["tags"] == ""

    def test_tags_on_stock_instance(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Tagged Instance Parent")
        inst = _create_instance(test_client, defn["id"])
        tag = _create_tag(test_client, "warranty")
        _set_tags(test_client, "stock_instance", inst["id"], [tag["id"]])

        status, body = _export(test_client, "stock_instances", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        from app.services.export import STOCK_INSTANCE_COLUMNS

        d = dict(zip(STOCK_INSTANCE_COLUMNS, rows[0], strict=False))
        assert d["tags"] == "warranty"

    def test_tags_on_location(self, test_client: TestClient) -> None:
        loc = _create_location(test_client, "Tagged Location")
        tag = _create_tag(test_client, "storage")
        _set_tags(test_client, "location", loc["id"], [tag["id"]])

        status, body = _export(test_client, "locations", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        from app.services.export import LOCATION_COLUMNS

        d = dict(zip(LOCATION_COLUMNS, rows[0], strict=False))
        assert d["tags"] == "storage"


# ---------------------------------------------------------------------------
# 5. custom_fields column — serialised as JSON string
# ---------------------------------------------------------------------------


class TestCustomFieldsColumn:
    """custom_fields exported as a JSON string column."""

    def test_custom_fields_on_definition(self, test_client: TestClient) -> None:
        _create_definition(
            test_client,
            "Custom Widget",
            custom_fields={"voltage": "220", "brand": "Acme"},
        )

        status, body = _export(test_client, "item_definitions", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        from app.services.export import ITEM_DEFINITION_COLUMNS

        d = dict(zip(ITEM_DEFINITION_COLUMNS, rows[0], strict=False))
        # custom_fields cell is a JSON string; parse and verify.
        cf = json.loads(d["custom_fields"])
        assert cf["voltage"] == "220"
        assert cf["brand"] == "Acme"

    def test_null_custom_fields_yields_empty_cell(self, test_client: TestClient) -> None:
        _create_definition(test_client, "No Custom Fields")

        status, body = _export(test_client, "item_definitions", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        from app.services.export import ITEM_DEFINITION_COLUMNS

        d = dict(zip(ITEM_DEFINITION_COLUMNS, rows[0], strict=False))
        assert d["custom_fields"] == ""

    def test_custom_fields_on_instance(self, test_client: TestClient) -> None:
        defn = _create_definition(test_client, "Measured Item")
        _create_instance(
            test_client,
            defn["id"],
            custom_fields={"capacity_gb": 256, "interface": "USB-C"},
        )

        status, body = _export(test_client, "stock_instances", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        from app.services.export import STOCK_INSTANCE_COLUMNS

        d = dict(zip(STOCK_INSTANCE_COLUMNS, rows[0], strict=False))
        cf = json.loads(d["custom_fields"])
        assert cf["capacity_gb"] == 256
        assert cf["interface"] == "USB-C"

    def test_custom_fields_in_json_export(self, test_client: TestClient) -> None:
        """custom_fields in JSON export is a JSON string (not a nested object)."""
        _create_definition(
            test_client,
            "JSON CF Item",
            custom_fields={"key": "val"},
        )

        status, body = _export(test_client, "item_definitions", "json")
        assert status == 200
        records = json.loads(body)
        assert len(records) == 1
        # custom_fields in the JSON export is a string column, consistent with CSV.
        assert isinstance(records[0]["custom_fields"], str)
        assert json.loads(records[0]["custom_fields"]) == {"key": "val"}


# ---------------------------------------------------------------------------
# 6. CSV escaping correctness
# ---------------------------------------------------------------------------


class TestCSVEscaping:
    """Values containing comma, double-quote, and newline survive a CSV round-trip."""

    def test_value_with_comma(self, test_client: TestClient) -> None:
        _create_location(test_client, "London, UK")

        status, body = _export(test_client, "locations", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        from app.services.export import LOCATION_COLUMNS

        d = dict(zip(LOCATION_COLUMNS, rows[0], strict=False))
        assert d["name"] == "London, UK"

    def test_value_with_double_quote(self, test_client: TestClient) -> None:
        _create_location(test_client, 'Shelf "A"')

        status, body = _export(test_client, "locations", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        from app.services.export import LOCATION_COLUMNS

        d = dict(zip(LOCATION_COLUMNS, rows[0], strict=False))
        assert d["name"] == 'Shelf "A"'

    def test_value_with_newline_in_description(self, test_client: TestClient) -> None:
        _create_location(test_client, "Storage", description="Line 1\nLine 2")

        status, body = _export(test_client, "locations", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        from app.services.export import LOCATION_COLUMNS

        d = dict(zip(LOCATION_COLUMNS, rows[0], strict=False))
        assert d["description"] == "Line 1\nLine 2"

    def test_combined_comma_quote_newline_in_name(self, test_client: TestClient) -> None:
        """Name containing both comma, double-quote, and newline survives round-trip."""
        tricky = 'A, B\n"C"'
        _create_location(test_client, tricky)

        status, body = _export(test_client, "locations", "csv")
        assert status == 200
        _, rows = _parse_csv(body)
        from app.services.export import LOCATION_COLUMNS

        # Round-trip through csv.reader must preserve the exact value.
        d = dict(zip(LOCATION_COLUMNS, rows[0], strict=False))
        assert d["name"] == tricky


# ---------------------------------------------------------------------------
# 7. JSON shape — same keys/values as CSV columns
# ---------------------------------------------------------------------------


class TestJSONShape:
    """JSON export has the same keys as CSV columns and equivalent values."""

    def test_json_keys_match_csv_columns_for_definitions(self, test_client: TestClient) -> None:
        from app.services.export import ITEM_DEFINITION_COLUMNS

        _create_definition(test_client, "Shape Check")
        _, body = _export(test_client, "item_definitions", "json")
        records = json.loads(body)
        assert len(records) == 1
        assert list(records[0].keys()) == ITEM_DEFINITION_COLUMNS

    def test_json_keys_match_csv_columns_for_instances(self, test_client: TestClient) -> None:
        from app.services.export import STOCK_INSTANCE_COLUMNS

        defn = _create_definition(test_client, "Shape Instance Parent")
        _create_instance(test_client, defn["id"])
        _, body = _export(test_client, "stock_instances", "json")
        records = json.loads(body)
        assert len(records) == 1
        assert list(records[0].keys()) == STOCK_INSTANCE_COLUMNS

    def test_json_keys_match_csv_columns_for_locations(self, test_client: TestClient) -> None:
        from app.services.export import LOCATION_COLUMNS

        _create_location(test_client, "Shape Room")
        _, body = _export(test_client, "locations", "json")
        records = json.loads(body)
        assert len(records) == 1
        assert list(records[0].keys()) == LOCATION_COLUMNS

    def test_json_name_value_matches_csv(self, test_client: TestClient) -> None:
        """The ``name`` field in JSON equals the CSV cell value."""
        from app.services.export import ITEM_DEFINITION_COLUMNS

        _create_definition(test_client, "ExactMatch")

        _, csv_body = _export(test_client, "item_definitions", "csv")
        _, rows = _parse_csv(csv_body)
        d_csv = dict(zip(ITEM_DEFINITION_COLUMNS, rows[0], strict=False))

        _, json_body = _export(test_client, "item_definitions", "json")
        rec = json.loads(json_body)[0]

        assert str(rec["id"]) == d_csv["id"]
        assert rec["name"] == d_csv["name"]

    def test_json_empty_db_is_valid_array(self, test_client: TestClient) -> None:
        """An empty DB exports as an empty JSON array ``[]``."""
        _, body = _export(test_client, "locations", "json")
        assert json.loads(body) == []


# ---------------------------------------------------------------------------
# 8. Bad entity / bad format → 422
# ---------------------------------------------------------------------------


class TestValidationErrors:
    """Bad entity or format → 422 validation.invalid_input."""

    def test_bad_entity_returns_422(self, test_client: TestClient) -> None:
        resp = test_client.get("/api/export/not_an_entity", params={"format": "csv"})
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_bad_format_returns_422(self, test_client: TestClient) -> None:
        resp = test_client.get("/api/export/item_definitions", params={"format": "xml"})
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_bad_entity_and_format_returns_422(self, test_client: TestClient) -> None:
        """When both entity and format are bad, the entity check fires first."""
        resp = test_client.get("/api/export/bogus", params={"format": "toml"})
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"


# ---------------------------------------------------------------------------
# 9. Unauthenticated → 401
# ---------------------------------------------------------------------------


class TestUnauthenticated:
    """GET /export/{entity} requires a valid session."""

    def test_no_session_returns_401(self, test_client: TestClient) -> None:
        from fastapi.testclient import TestClient as FreshClient

        app = test_client.app
        with FreshClient(app) as anon:
            resp = anon.get("/api/export/item_definitions", params={"format": "csv"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 10. Streaming — export() returns a generator
# ---------------------------------------------------------------------------


class TestStreaming:
    """ExportService.export() returns a generator (not a materialised list)."""

    def test_csv_iterator_is_a_generator(self, test_client: TestClient) -> None:
        """The return value of ExportService.export(..., 'csv') is a generator."""
        from sqlalchemy.orm import sessionmaker as SM

        from app.db.base import get_engine
        from app.services.export import ExportService

        engine = get_engine()
        factory = SM(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            svc = ExportService(db)
            result = svc.export("item_definitions", "csv")
            assert inspect.isgenerator(result), (
                "ExportService.export() must return a generator for streaming"
            )
        finally:
            db.close()

    def test_json_iterator_is_a_generator(self, test_client: TestClient) -> None:
        from sqlalchemy.orm import sessionmaker as SM

        from app.db.base import get_engine
        from app.services.export import ExportService

        engine = get_engine()
        factory = SM(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            svc = ExportService(db)
            result = svc.export("locations", "json")
            assert inspect.isgenerator(result), (
                "ExportService.export() must return a generator for streaming"
            )
        finally:
            db.close()

    def test_api_response_has_streaming_content_type(self, test_client: TestClient) -> None:
        """The export endpoint returns the correct media type."""
        resp = test_client.get("/api/export/item_definitions", params={"format": "csv"})
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")

        resp2 = test_client.get("/api/export/item_definitions", params={"format": "json"})
        assert resp2.status_code == 200
        assert "application/json" in resp2.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# 11. Decimal and date formatting — stable, locale-independent
# ---------------------------------------------------------------------------


class TestDecimalAndDateFormatting:
    """Decimal and date values are rendered with stable, locale-independent strings."""

    def test_purchase_price_decimal_format(self, test_client: TestClient) -> None:
        """purchase_price Decimal → locale-independent string (no thousands separators)."""
        defn = _create_definition(test_client, "Priced Item")
        _create_instance(
            test_client,
            defn["id"],
            purchase_price="1234.99",
        )

        _, body = _export(test_client, "stock_instances", "csv")
        _, rows = _parse_csv(body)
        from app.services.export import STOCK_INSTANCE_COLUMNS

        d = dict(zip(STOCK_INSTANCE_COLUMNS, rows[0], strict=False))
        price_str = d["purchase_price"]
        # Must not contain locale separators (comma as thousands); must be parseable.
        assert "," not in price_str
        assert Decimal(price_str) == Decimal("1234.99")

    def test_best_before_date_iso_format(self, test_client: TestClient) -> None:
        """best_before_date → ISO 8601 date string (YYYY-MM-DD)."""
        defn = _create_definition(test_client, "Perishable")
        _create_instance(
            test_client,
            defn["id"],
            best_before_date="2027-12-31",
        )

        _, body = _export(test_client, "stock_instances", "csv")
        _, rows = _parse_csv(body)
        from app.services.export import STOCK_INSTANCE_COLUMNS

        d = dict(zip(STOCK_INSTANCE_COLUMNS, rows[0], strict=False))
        assert d["best_before_date"] == "2027-12-31"

    def test_json_decimal_is_string(self, test_client: TestClient) -> None:
        """In JSON export, Decimal values are strings (no float precision loss)."""
        defn = _create_definition(test_client, "Priced JSON")
        _create_instance(
            test_client,
            defn["id"],
            purchase_price="99.50",
        )

        _, body = _export(test_client, "stock_instances", "json")
        records = json.loads(body)
        assert len(records) == 1
        price = records[0]["purchase_price"]
        # JSON value must be a string (not a float) and parse back correctly.
        assert isinstance(price, str)
        assert Decimal(price) == Decimal("99.50")

    def test_json_date_is_iso_string(self, test_client: TestClient) -> None:
        """In JSON export, date values are ISO 8601 strings."""
        defn = _create_definition(test_client, "Dated JSON")
        _create_instance(
            test_client,
            defn["id"],
            best_before_date="2028-06-15",
        )

        _, body = _export(test_client, "stock_instances", "json")
        records = json.loads(body)
        assert records[0]["best_before_date"] == "2028-06-15"


# ---------------------------------------------------------------------------
# 12. Content-Disposition header
# ---------------------------------------------------------------------------


class TestContentDisposition:
    """The export endpoint includes the correct Content-Disposition header."""

    def test_csv_content_disposition_filename(self, test_client: TestClient) -> None:
        resp = test_client.get("/api/export/item_definitions", params={"format": "csv"})
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd
        today = date.today().isoformat()
        assert f"item_definitions-{today}.csv" in cd

    def test_json_content_disposition_filename(self, test_client: TestClient) -> None:
        resp = test_client.get("/api/export/locations", params={"format": "json"})
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd
        today = date.today().isoformat()
        assert f"locations-{today}.json" in cd


# ---------------------------------------------------------------------------
# 13. All three entities return 200 for both formats (smoke)
# ---------------------------------------------------------------------------


class TestAllEntitiesAndFormats:
    """Quick smoke: all valid entity × format combinations return 200."""

    @pytest.mark.parametrize(
        "entity",
        ["item_definitions", "stock_instances", "locations"],
    )
    @pytest.mark.parametrize("fmt", ["csv", "json"])
    def test_entity_format_combination(
        self,
        test_client: TestClient,
        entity: str,
        fmt: str,
    ) -> None:
        status, _ = _export(test_client, entity, fmt)
        assert status == 200
