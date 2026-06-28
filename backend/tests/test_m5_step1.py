"""Tests for M5 Step 1: media storage and generic attachments.

Coverage
--------
- Identical bytes → ONE media_files row + TWO attachment rows (de-duplication).
- Deleting one of two attachments → media_files row + physical file still exist.
- Deleting last attachment → media_files row and physical file both removed.
- Owner cascade: deleting an item_definition cascades attachments and drops
  unreferenced files.
- Image validation + downscale: an over-large image is resized to the edge cap;
  width/height are recorded on the media_files row.
- attachment.file_too_large (413) for uploads exceeding 25 MB.
- attachment.unsupported_type (415) for SVG, corrupt bytes, and unknown types.
- Persisted content_type is the validated value, not the raw client header.
- validation.invalid_input (422) for an unknown model_type.
- Owner not-found (404) for a valid model_type but non-existent model_id.
- Failed physical unlink is logged but does not raise.
- Migrations 0020 and 0021 upgrade and downgrade cleanly on a DB at 0019.
"""

from __future__ import annotations

import io
import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_temp_db_url() -> tuple[str, Path]:
    """Return (url, path) for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m5_step1_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()
    return f"sqlite:///{path_str}", path


def _make_jpeg(width: int = 100, height: int = 100) -> bytes:
    """Create a minimal valid JPEG in memory."""
    from PIL import Image

    img = Image.new("RGB", (width, height), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_png(width: int = 100, height: int = 100) -> bytes:
    """Create a minimal valid PNG in memory."""
    from PIL import Image

    img = Image.new("RGB", (width, height), color=(0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Shared fixture infrastructure
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
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m5-step1")
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
    # Use tmp_path for media storage — never touch the real ./data directory.
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    import importlib

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.attachment as attachment_mod

    # Re-register all models with a fresh Base so create_all() includes
    # both pre-existing and new M5 tables.
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

    # Clear settings cache so DATA_DIR monkeypatch is seen by the factory.
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

            # Seed system item_kinds (normally done by Alembic migration 0006).
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
# Convenience helpers that require an authenticated client
# ---------------------------------------------------------------------------


def _create_definition(client: TestClient, name: str) -> dict:  # type: ignore[type-arg]
    """POST /api/definitions and return the response JSON dict."""
    resp = client.post("/api/definitions", json={"name": name})
    assert resp.status_code == 201, f"create_definition failed: {resp.json()}"
    return resp.json()  # type: ignore[return-value]


def _upload(
    client: TestClient,
    *,
    model_type: str,
    model_id: int,
    file_bytes: bytes,
    filename: str = "file.jpg",
    content_type: str = "image/jpeg",
    title: str | None = None,
) -> dict:  # type: ignore[type-arg]
    """POST /api/attachments and return the JSON dict."""
    data: dict[str, str] = {  # type: ignore[type-arg]
        "model_type": model_type,
        "model_id": str(model_id),
    }
    if title is not None:
        data["title"] = title
    resp = client.post(
        "/api/attachments",
        files={"file": (filename, file_bytes, content_type)},
        data=data,
    )
    return resp.json()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. De-duplication — identical bytes → ONE media_files row
# ---------------------------------------------------------------------------


class TestDeduplication:
    """Uploading the same bytes twice creates ONE media_files row + TWO attachment rows."""

    def test_same_bytes_one_media_file(self, test_client: TestClient) -> None:
        jpeg = _make_jpeg()
        defn = _create_definition(test_client, "Widget A")
        defn_id = defn["id"]

        # Upload the same bytes twice.
        r1 = test_client.post(
            "/api/attachments",
            files={"file": ("a.jpg", jpeg, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert r1.status_code == 201

        r2 = test_client.post(
            "/api/attachments",
            files={"file": ("b.jpg", jpeg, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert r2.status_code == 201

        sha1 = r1.json()["media"]["sha256"]
        sha2 = r2.json()["media"]["sha256"]
        # Same content → same hash → same MediaFile.
        assert sha1 == sha2
        # But two distinct attachment rows.
        assert r1.json()["id"] != r2.json()["id"]

    def test_same_bytes_two_attachments_listed(self, test_client: TestClient) -> None:
        jpeg = _make_jpeg()
        defn_id = _create_definition(test_client, "Widget B")["id"]
        for _ in range(2):
            resp = test_client.post(
                "/api/attachments",
                files={"file": ("x.jpg", jpeg, "image/jpeg")},
                data={"model_type": "item_definition", "model_id": str(defn_id)},
            )
            assert resp.status_code == 201

        list_resp = test_client.get(
            "/api/attachments",
            params={"model_type": "item_definition", "model_id": defn_id},
        )
        assert list_resp.status_code == 200
        atts = list_resp.json()
        assert len(atts) == 2


# ---------------------------------------------------------------------------
# 2. Reference-counting delete semantics
# ---------------------------------------------------------------------------


class TestReferenceCountingDelete:
    """Delete behaviours: last-reference cleanup vs. keep-alive."""

    def test_delete_non_last_keeps_file(self, test_client: TestClient, tmp_path: Path) -> None:
        """Deleting one of two attachment rows does NOT remove the physical file."""
        jpeg = _make_jpeg()
        defn_id = _create_definition(test_client, "Widget C")["id"]

        r1 = test_client.post(
            "/api/attachments",
            files={"file": ("x.jpg", jpeg, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        r2 = test_client.post(
            "/api/attachments",
            files={"file": ("y.jpg", jpeg, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert r1.status_code == 201
        assert r2.status_code == 201

        sha = r1.json()["media"]["sha256"]
        file_path = tmp_path / "media" / sha[:2] / sha

        # Physical file must exist after upload.
        assert file_path.exists(), f"Expected file at {file_path}"

        # Delete the FIRST attachment (still one reference left).
        del_resp = test_client.delete(f"/api/attachments/{r1.json()['id']}")
        assert del_resp.status_code == 204

        # File must still exist (second attachment still references it).
        assert file_path.exists(), "Physical file must survive if second reference exists"

    def test_delete_last_removes_file(self, test_client: TestClient, tmp_path: Path) -> None:
        """Deleting the last attachment removes the media_files row + physical file."""
        jpeg = _make_jpeg()
        defn_id = _create_definition(test_client, "Widget D")["id"]

        r1 = test_client.post(
            "/api/attachments",
            files={"file": ("z.jpg", jpeg, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert r1.status_code == 201

        sha = r1.json()["media"]["sha256"]
        file_path = tmp_path / "media" / sha[:2] / sha
        assert file_path.exists(), "File must exist after upload"

        # Delete the ONLY attachment.
        del_resp = test_client.delete(f"/api/attachments/{r1.json()['id']}")
        assert del_resp.status_code == 204

        # Physical file must be gone (last reference removed).
        assert not file_path.exists(), "Physical file must be removed after last reference deleted"


# ---------------------------------------------------------------------------
# 3. Owner cascade
# ---------------------------------------------------------------------------


class TestOwnerCascade:
    """Deleting an owner cascades to its attachments and unreferenced files."""

    def test_delete_definition_cascades(self, test_client: TestClient, tmp_path: Path) -> None:
        """Deleting an item_definition removes all its attachments and media files."""
        jpeg = _make_jpeg()
        defn_id = _create_definition(test_client, "Cascade Widget")["id"]

        # Upload two attachments.
        r1 = test_client.post(
            "/api/attachments",
            files={"file": ("a.jpg", jpeg, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        r2 = test_client.post(
            "/api/attachments",
            files={"file": ("b.jpg", _make_png(), "image/png")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert r1.status_code == 201
        assert r2.status_code == 201

        sha1 = r1.json()["media"]["sha256"]
        sha2 = r2.json()["media"]["sha256"]
        file1 = tmp_path / "media" / sha1[:2] / sha1
        file2 = tmp_path / "media" / sha2[:2] / sha2
        assert file1.exists()
        assert file2.exists()

        # Delete the owner.
        del_resp = test_client.delete(f"/api/definitions/{defn_id}")
        assert del_resp.status_code == 204

        # Both physical files must be removed.
        assert not file1.exists(), "JPEG file must be cleaned up after owner delete"
        assert not file2.exists(), "PNG file must be cleaned up after owner delete"

    def test_delete_stock_instance_cascades(self, test_client: TestClient, tmp_path: Path) -> None:
        """Deleting a stock_instance removes all its attachments and media files."""
        jpeg = _make_jpeg()
        # Create a definition first (required FK for stock_instance).
        defn_id = _create_definition(test_client, "Instance Cascade Widget")["id"]

        # Create a stock_instance.
        inst_resp = test_client.post("/api/instances", json={"definition_id": defn_id})
        assert inst_resp.status_code == 201
        inst_id = inst_resp.json()["id"]

        # Upload an attachment to the instance.
        r = test_client.post(
            "/api/attachments",
            files={"file": ("photo.jpg", jpeg, "image/jpeg")},
            data={"model_type": "stock_instance", "model_id": str(inst_id)},
        )
        assert r.status_code == 201

        sha = r.json()["media"]["sha256"]
        file_path = tmp_path / "media" / sha[:2] / sha
        assert file_path.exists(), "File must exist after upload"

        # Delete the stock_instance — this should cascade-delete the attachment
        # and (since it's the last reference) the physical file.
        del_resp = test_client.delete(f"/api/instances/{inst_id}")
        assert del_resp.status_code == 204

        assert not file_path.exists(), (
            "Physical file must be removed after stock_instance delete (cascade)"
        )

    def test_delete_location_cascades(self, test_client: TestClient, tmp_path: Path) -> None:
        """Deleting a location removes all its attachments and media files."""
        jpeg = _make_jpeg()

        # Create a location.
        loc_resp = test_client.post("/api/locations", json={"name": "Location Cascade"})
        assert loc_resp.status_code == 201
        loc_id = loc_resp.json()["id"]

        # Upload an attachment to the location.
        r = test_client.post(
            "/api/attachments",
            files={"file": ("photo.jpg", jpeg, "image/jpeg")},
            data={"model_type": "location", "model_id": str(loc_id)},
        )
        assert r.status_code == 201

        sha = r.json()["media"]["sha256"]
        file_path = tmp_path / "media" / sha[:2] / sha
        assert file_path.exists(), "File must exist after upload"

        # Delete the location — cascade should clean up the attachment and file.
        del_resp = test_client.delete(f"/api/locations/{loc_id}")
        assert del_resp.status_code == 204

        assert not file_path.exists(), (
            "Physical file must be removed after location delete (cascade)"
        )

    def test_shared_media_file_survives_partial_cascade(
        self, test_client: TestClient, tmp_path: Path
    ) -> None:
        """A media file shared across owners survives when only ONE owner is deleted."""
        jpeg = _make_jpeg()

        defn1_id = _create_definition(test_client, "Owner A")["id"]
        defn2_id = _create_definition(test_client, "Owner B")["id"]

        # Both owners attach the SAME bytes.
        r1 = test_client.post(
            "/api/attachments",
            files={"file": ("shared.jpg", jpeg, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": str(defn1_id)},
        )
        r2 = test_client.post(
            "/api/attachments",
            files={"file": ("shared.jpg", jpeg, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": str(defn2_id)},
        )
        assert r1.status_code == 201
        assert r2.status_code == 201

        sha = r1.json()["media"]["sha256"]
        assert sha == r2.json()["media"]["sha256"]
        file_path = tmp_path / "media" / sha[:2] / sha
        assert file_path.exists()

        # Delete ONLY the first owner.
        del_resp = test_client.delete(f"/api/definitions/{defn1_id}")
        assert del_resp.status_code == 204

        # File must still exist (defn2 still references it).
        assert file_path.exists(), "Shared file must survive when only one owner is deleted"


# ---------------------------------------------------------------------------
# 4. Image validation + downscale
# ---------------------------------------------------------------------------


class TestImageValidationAndDownscale:
    """Image is validated with Pillow; over-large images are downscaled to the edge cap."""

    def test_small_jpeg_dimensions_recorded(self, test_client: TestClient) -> None:
        """Uploading a 100×100 JPEG records width=100 and height=100."""
        jpeg = _make_jpeg(100, 100)
        defn_id = _create_definition(test_client, "Dims Widget")["id"]
        resp = test_client.post(
            "/api/attachments",
            files={"file": ("photo.jpg", jpeg, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert resp.status_code == 201
        media = resp.json()["media"]
        assert media["width"] == 100
        assert media["height"] == 100

    def test_large_image_downscaled_to_2048(self, test_client: TestClient) -> None:
        """Uploading a 4000×3000 PNG results in a stored image ≤ 2048px on longest edge."""
        big_png = _make_png(4000, 3000)
        defn_id = _create_definition(test_client, "Big Image Widget")["id"]
        resp = test_client.post(
            "/api/attachments",
            files={"file": ("big.png", big_png, "image/png")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert resp.status_code == 201
        media = resp.json()["media"]
        # 4000×3000 → scale = 2048/4000 → 2048×1536
        assert media["width"] == 2048
        assert media["height"] == 1536
        # Byte size is the re-encoded (smaller) PNG, not the original.
        assert media["byte_size"] < len(big_png)

    def test_pdf_dimensions_are_null(self, test_client: TestClient) -> None:
        """Uploading a PDF records width=None and height=None (no image dimensions)."""
        # Minimal PDF header.
        pdf_bytes = b"%PDF-1.4\n%%EOF"
        defn_id = _create_definition(test_client, "PDF Widget")["id"]
        resp = test_client.post(
            "/api/attachments",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert resp.status_code == 201
        media = resp.json()["media"]
        assert media["width"] is None
        assert media["height"] is None


# ---------------------------------------------------------------------------
# 5. File size limit
# ---------------------------------------------------------------------------


class TestFileSizeLimit:
    """Files larger than 25 MB are rejected with attachment.file_too_large (413)."""

    def test_oversize_file_returns_413(self, test_client: TestClient) -> None:
        """A 26 MB dummy payload returns 413 with attachment.file_too_large."""
        from app.services.media_storage import MAX_BYTE_SIZE

        oversize = b"x" * (MAX_BYTE_SIZE + 1)
        defn_id = _create_definition(test_client, "Oversize Widget")["id"]
        resp = test_client.post(
            "/api/attachments",
            files={"file": ("large.bin", oversize, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert resp.status_code == 413
        body = resp.json()
        assert body["code"] == "attachment.file_too_large"


# ---------------------------------------------------------------------------
# 6. Unsupported / invalid content types
# ---------------------------------------------------------------------------


class TestUnsupportedTypes:
    """SVG, corrupt image bytes, and unrecognised types are rejected with 415."""

    def test_svg_returns_415(self, test_client: TestClient) -> None:
        """SVG files are always rejected (active content)."""
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="10"/></svg>'
        defn_id = _create_definition(test_client, "SVG Widget")["id"]
        resp = test_client.post(
            "/api/attachments",
            files={"file": ("icon.svg", svg, "image/svg+xml")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert resp.status_code == 415
        assert resp.json()["code"] == "attachment.unsupported_type"

    def test_corrupt_image_bytes_returns_415(self, test_client: TestClient) -> None:
        """Random bytes declared as image/jpeg fail Pillow verify → 415."""
        corrupt = b"\xff\xd8\xff" + b"\x00" * 100  # broken JPEG
        defn_id = _create_definition(test_client, "Corrupt Widget")["id"]
        resp = test_client.post(
            "/api/attachments",
            files={"file": ("bad.jpg", corrupt, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert resp.status_code == 415
        assert resp.json()["code"] == "attachment.unsupported_type"

    def test_unknown_non_image_type_returns_415(self, test_client: TestClient) -> None:
        """A MIME type not in the allow-list (e.g. application/zip) returns 415."""
        defn_id = _create_definition(test_client, "Zip Widget")["id"]
        resp = test_client.post(
            "/api/attachments",
            files={"file": ("archive.zip", b"PK\x03\x04", "application/zip")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert resp.status_code == 415
        assert resp.json()["code"] == "attachment.unsupported_type"


# ---------------------------------------------------------------------------
# 7. Persisted content_type is the validated value
# ---------------------------------------------------------------------------


class TestValidatedContentType:
    """The content_type stored in media_files is our validated value, not the raw header."""

    def test_jpeg_content_type_is_image_jpeg(self, test_client: TestClient) -> None:
        """A JPEG upload stores 'image/jpeg' regardless of the exact client header."""
        jpeg = _make_jpeg()
        defn_id = _create_definition(test_client, "CT Widget")["id"]
        # Client sends a header with extra parameters; we strip them.
        resp = test_client.post(
            "/api/attachments",
            files={"file": ("photo.jpg", jpeg, "image/jpeg; charset=binary")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert resp.status_code == 201
        assert resp.json()["media"]["content_type"] == "image/jpeg"

    def test_pdf_content_type_is_application_pdf(self, test_client: TestClient) -> None:
        """A PDF upload stores 'application/pdf'."""
        pdf_bytes = b"%PDF-1.4\n%%EOF"
        defn_id = _create_definition(test_client, "PDF CT Widget")["id"]
        resp = test_client.post(
            "/api/attachments",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert resp.status_code == 201
        assert resp.json()["media"]["content_type"] == "application/pdf"


# ---------------------------------------------------------------------------
# 8. Owner-type validation
# ---------------------------------------------------------------------------


class TestOwnerValidation:
    """Invalid or unknown owners return the correct error codes."""

    def test_bad_model_type_returns_422(self, test_client: TestClient) -> None:
        """An unrecognised model_type returns 422 with validation.invalid_input."""
        jpeg = _make_jpeg()
        resp = test_client.post(
            "/api/attachments",
            files={"file": ("x.jpg", jpeg, "image/jpeg")},
            data={"model_type": "bad_type", "model_id": "1"},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation.invalid_input"

    def test_unknown_item_definition_id_returns_404(self, test_client: TestClient) -> None:
        """A valid model_type but non-existent model_id returns 404 with item_definition.not_found."""
        jpeg = _make_jpeg()
        resp = test_client.post(
            "/api/attachments",
            files={"file": ("x.jpg", jpeg, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": "99999"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "item_definition.not_found"

    def test_unknown_location_id_returns_404(self, test_client: TestClient) -> None:
        """model_type='location' with non-existent id returns 404 with location.not_found."""
        jpeg = _make_jpeg()
        resp = test_client.post(
            "/api/attachments",
            files={"file": ("x.jpg", jpeg, "image/jpeg")},
            data={"model_type": "location", "model_id": "99999"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "location.not_found"


# ---------------------------------------------------------------------------
# 9. PATCH and basic CRUD
# ---------------------------------------------------------------------------


class TestAttachmentCRUD:
    """Basic CRUD: list, update, delete."""

    def test_patch_title(self, test_client: TestClient) -> None:
        """PATCH /attachments/{id} updates the title."""
        jpeg = _make_jpeg()
        defn_id = _create_definition(test_client, "CRUD Widget")["id"]
        r = test_client.post(
            "/api/attachments",
            files={"file": ("x.jpg", jpeg, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": str(defn_id), "title": "Before"},
        )
        assert r.status_code == 201
        att_id = r.json()["id"]

        patch_resp = test_client.patch(
            f"/api/attachments/{att_id}",
            json={"title": "After"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["title"] == "After"

    def test_delete_returns_204(self, test_client: TestClient) -> None:
        """DELETE /attachments/{id} returns 204."""
        jpeg = _make_jpeg()
        defn_id = _create_definition(test_client, "Del Widget")["id"]
        r = test_client.post(
            "/api/attachments",
            files={"file": ("x.jpg", jpeg, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert r.status_code == 201
        att_id = r.json()["id"]

        del_resp = test_client.delete(f"/api/attachments/{att_id}")
        assert del_resp.status_code == 204

    def test_delete_nonexistent_returns_404(self, test_client: TestClient) -> None:
        """DELETE /attachments/{id} for a missing attachment returns 404."""
        resp = test_client.delete("/api/attachments/99999")
        assert resp.status_code == 404
        assert resp.json()["code"] == "attachment.not_found"

    def test_media_url_pattern(self, test_client: TestClient) -> None:
        """AttachmentResponse.media.url follows the pattern /media/<sha[:2]>/<sha>."""
        jpeg = _make_jpeg()
        defn_id = _create_definition(test_client, "URL Widget")["id"]
        r = test_client.post(
            "/api/attachments",
            files={"file": ("x.jpg", jpeg, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert r.status_code == 201
        media = r.json()["media"]
        sha = media["sha256"]
        expected_url = f"/media/{sha[:2]}/{sha}"
        assert media["url"] == expected_url


# ---------------------------------------------------------------------------
# 10. Media serving headers (B1)
# ---------------------------------------------------------------------------


class TestMediaServing:
    """GET /media/<shard>/<digest> returns correct content_type and safe headers."""

    def test_image_served_with_stored_content_type(self, test_client: TestClient) -> None:
        """A JPEG image is served with Content-Type: image/jpeg (not octet-stream)."""
        jpeg = _make_jpeg()
        defn_id = _create_definition(test_client, "Serve JPEG Widget")["id"]
        r = test_client.post(
            "/api/attachments",
            files={"file": ("photo.jpg", jpeg, "image/jpeg")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert r.status_code == 201
        media = r.json()["media"]
        url = media["url"]

        serve_resp = test_client.get(url)
        assert serve_resp.status_code == 200
        assert serve_resp.headers["content-type"].startswith("image/jpeg")
        assert serve_resp.headers.get("x-content-type-options") == "nosniff"
        # Images must NOT have Content-Disposition: attachment (they render inline).
        assert "content-disposition" not in serve_resp.headers

    def test_non_image_served_with_attachment_disposition(self, test_client: TestClient) -> None:
        """A PDF is served with Content-Disposition: attachment (not inline)."""
        pdf_bytes = b"%PDF-1.4\n%%EOF"
        defn_id = _create_definition(test_client, "Serve PDF Widget")["id"]
        r = test_client.post(
            "/api/attachments",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
            data={"model_type": "item_definition", "model_id": str(defn_id)},
        )
        assert r.status_code == 201
        url = r.json()["media"]["url"]

        serve_resp = test_client.get(url)
        assert serve_resp.status_code == 200
        assert serve_resp.headers["content-type"].startswith("application/pdf")
        assert serve_resp.headers.get("x-content-type-options") == "nosniff"
        assert "attachment" in serve_resp.headers.get("content-disposition", "")

    def test_unknown_digest_returns_404(self, test_client: TestClient) -> None:
        """Requesting /media/<shard>/<unknown-digest> returns 404."""
        fake_sha = "ab" * 32  # 64 hex chars
        resp = test_client.get(f"/media/{fake_sha[:2]}/{fake_sha}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 12. Failed physical unlink — logged, not raised (unit test)
# ---------------------------------------------------------------------------


class TestFailedUnlinkSilent:
    """MediaStorage.delete_physical on a non-existent path logs but does not raise."""

    def _make_db_session(self) -> Session:
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
        importlib.reload(audit_log_mod)

        from app.db.base import Base

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn: object, _: object) -> None:
            import sqlite3

            if isinstance(dbapi_conn, sqlite3.Connection):
                dbapi_conn.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        return factory()

    def test_failed_unlink_logs_and_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """unlink_post_commit must swallow OSError from path.unlink and log a warning."""
        import logging
        from pathlib import Path as _Path

        from app.services.attachment import unlink_post_commit

        # Create a real file so the path exists.
        fake_file = tmp_path / "media" / "ab" / ("a" * 64)
        fake_file.parent.mkdir(parents=True, exist_ok=True)
        fake_file.write_bytes(b"data")

        # Monkeypatch Path.unlink to raise OSError so the except branch fires.
        original_unlink = _Path.unlink

        def _raise_unlink(self: _Path, missing_ok: bool = False) -> None:  # noqa: ARG001, FBT002
            raise OSError("simulated unlink failure")

        monkeypatch.setattr(_Path, "unlink", _raise_unlink)

        with caplog.at_level(logging.WARNING, logger="app.services.attachment"):
            # Must not raise even when unlink fails.
            unlink_post_commit([fake_file])

        # Restore unlink so cleanup works.
        monkeypatch.setattr(_Path, "unlink", original_unlink)

        assert any("Failed to unlink" in r.message for r in caplog.records), (
            "Expected a warning log entry about the failed unlink"
        )


# ---------------------------------------------------------------------------
# 13. Migration round-trip
# ---------------------------------------------------------------------------


class TestMigrations0020And0021:
    """Migrations 0020 (media_files) and 0021 (attachments) round-trip cleanly."""

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

    def test_migration_0020_and_0021_up_down(self) -> None:
        """Upgrade through 0020 and 0021; downgrade back to 0019 cleanly."""
        from sqlalchemy import create_engine as sa_create_engine
        from sqlalchemy import inspect as sa_inspect

        fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_mig_0020_")
        os.close(fd)
        db_path = Path(path_str)
        db_path.unlink()
        url = f"sqlite:///{path_str}"

        try:
            # Upgrade to HEAD (includes 0020 and 0021).
            rc, output = self._run_alembic("upgrade", "head", url=url)
            assert rc == 0, f"alembic upgrade head failed:\n{output}"

            eng = sa_create_engine(url)
            tables = set(sa_inspect(eng).get_table_names())
            eng.dispose()
            assert "media_files" in tables, (
                f"media_files table missing after upgrade to head. Tables: {tables}"
            )
            assert "attachments" in tables, (
                f"attachments table missing after upgrade to head. Tables: {tables}"
            )

            # Downgrade to 0020 (removes attachments table).
            rc, output = self._run_alembic("downgrade", "0020", url=url)
            assert rc == 0, f"alembic downgrade to 0020 failed:\n{output}"

            eng = sa_create_engine(url)
            tables = set(sa_inspect(eng).get_table_names())
            eng.dispose()
            assert "attachments" not in tables, (
                "attachments table must be gone after downgrade to 0020"
            )
            assert "media_files" in tables, "media_files table must survive downgrade to 0020"

            # Downgrade to 0019 (removes media_files table).
            rc, output = self._run_alembic("downgrade", "0019", url=url)
            assert rc == 0, f"alembic downgrade to 0019 failed:\n{output}"

            eng = sa_create_engine(url)
            tables = set(sa_inspect(eng).get_table_names())
            eng.dispose()
            assert "media_files" not in tables, (
                "media_files table must be gone after downgrade to 0019"
            )

        finally:
            if db_path.exists():
                db_path.unlink()
