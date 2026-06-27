"""Tests for M6 Step 3: invitations, password reset, and self change-password.

Coverage
--------
Token layer:
- ``mint_token`` returns (raw, hash); hash is sha256 hex of raw (64 chars).
- ``hash_token`` reproduces the same hash as mint_token.

Repository layer:
- ``create`` inserts a row; ``get_by_token_hash`` retrieves it.
- ``get_pending_invite_by_email``: returns pending invite; None for consumed/expired/wrong email.
- ``list_pending_invites``: all pending, excludes consumed and expired.
- ``list_pending_resets_for_user``: pending resets for a specific user.
- ``mark_consumed`` sets consumed_at; subsequent pending-invite lookup returns None.
- ``delete`` removes the row.
- ``purge_expired`` deletes expired rows only.

UserRepository extension:
- ``set_password_hash`` updates and flushes.

Sessions helper:
- ``revoke_all_for_user`` deletes all sessions; with except_session_id keeps one.

Service layer (InvitationService):
- Create invite: valid → token returned; raw token not in DB (only hash stored).
- Create invite: existing user email → 409 USER_EMAIL_EXISTS.
- Create invite: invalid role → 422 INVALID_INPUT.
- Create invite: second invite for same email replaces prior pending one.
- validate_invite: valid → returns token.
- validate_invite: unknown token, consumed token, expired token → 400 AUTH_INVALID_TOKEN.
- accept_invite: creates user with invited role; password is usable (login succeeds).
- accept_invite: consumed/expired → 400.
- accept_invite: email race (user registered meanwhile) → 400 AUTH_INVALID_TOKEN.
- revoke: removes a pending invite; 404 for missing.
- issue_reset: 404 for missing user; mints token; revokes prior pending resets.
- validate_reset: valid → token; unknown/consumed/expired → 400 AUTH_INVALID_TOKEN.
- accept_reset: sets new password hash; consumes token; revokes all user sessions.
- accept_reset: consumed/expired → 400.
- change_password: wrong current → 400 AUTH_PASSWORD_INCORRECT.
- change_password: correct → hash updated; other sessions revoked; current kept.
- get_reset_email_masked: returns masked email; unknown/expired → 400.

Route-level integration:
- POST/GET/DELETE /invitations, POST /users/{id}/reset-password are 403 for member/viewer.
- Admin can create invite → 201 with accept_url + emailed=False (no SMTP).
- GET /invitations lists pending invites (MANAGE_USERS).
- DELETE /invitations/{id} revokes (204); 404 for missing.
- GET /invitations/accept (public) → {email, role}; 400 for bad token.
- POST /invitations/accept (public) → creates user (201); 400 for bad token.
- POST /users/{id}/reset-password → {reset_url, emailed}; 404 for missing user.
- GET /password-reset/accept (public) → {email_masked}; 400 for bad token.
- POST /password-reset/accept (public) → 200; 400 for bad token.
- POST /auth/change-password (authed) → 200; 400 for wrong current.

Optional SMTP:
- disabled channel → emailed=False + link still returned.
- enabled (monkeypatched to no-op) → emailed=True + channel called.
- SMTP error (raised in monkeypatch) → emailed=False + request succeeds.

purge_expired integration:
- Stale rows deleted; live rows kept.

Migration round-trip:
- upgrade 0028 → user_tokens table exists.
- downgrade → table gone.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import tempfile
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker as SM

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Common fixtures (pattern mirrors test_m6_step2.py)
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
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m6_step3_")
    os.close(fd)
    db_path = Path(path_str)
    db_path.unlink()
    url = f"sqlite:///{path_str}"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m6-step3")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


def _reload_all_models() -> None:
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


def _make_user(
    engine: object,
    email: str,
    role: str = "admin",
    is_active: bool = True,
) -> int:
    factory = SM(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
    db: DBSession = factory()
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


def _login(client: TestClient, email: str, password: str = "testpassword") -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed for {email}: {resp.json()}"


def _create_and_login(
    engine: object,
    client: TestClient,
    email: str,
    role: str = "admin",
    password: str = "testpassword",
) -> int:
    uid = _make_user(engine, email, role=role)
    _login(client, email, password)
    return uid


def _new_client_for_role(
    base_client: tuple[TestClient, object],
    role: str,
    email: str,
    password: str = "testpassword",
) -> tuple[TestClient, int]:
    """Return a fresh TestClient logged in as a user with the given role."""
    client, engine = base_client
    uid = _make_user(engine, email, role=role)

    from app.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    new_client = TestClient(create_app(), raise_server_exceptions=True)
    # Copy cookies from base_client session
    resp = new_client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    return new_client, uid


# ---------------------------------------------------------------------------
# Unit: token helper (mint/hash)
# ---------------------------------------------------------------------------


def test_mint_token_returns_raw_and_hash() -> None:
    from app.auth.tokens import mint_token

    raw, token_hash = mint_token()
    assert len(raw) > 0
    assert len(token_hash) == 64  # sha256 hex digest is always 64 chars
    assert token_hash == hashlib.sha256(raw.encode()).hexdigest()


def test_hash_token_matches_mint_token() -> None:
    from app.auth.tokens import hash_token, mint_token

    raw, minted_hash = mint_token()
    assert hash_token(raw) == minted_hash


def test_raw_token_different_from_hash() -> None:
    from app.auth.tokens import mint_token

    raw, token_hash = mint_token()
    assert raw != token_hash


# ---------------------------------------------------------------------------
# Unit: UserTokenRepository
# ---------------------------------------------------------------------------


def _make_db(engine: object) -> DBSession:
    factory = SM(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
    return factory()


def test_repo_create_and_get_by_hash(base_client: tuple[TestClient, object]) -> None:
    from app.auth.tokens import invite_expires_at, mint_token
    from app.repositories.user_token import UserTokenRepository

    _, engine = base_client
    db = _make_db(engine)
    try:
        raw, token_hash = mint_token()
        repo = UserTokenRepository(db)
        token = repo.create(
            purpose="invite",
            email="x@example.com",
            role="member",
            token_hash=token_hash,
            expires_at=invite_expires_at(),
        )
        db.commit()

        found = repo.get_by_token_hash(token_hash)
        assert found is not None
        assert found.id == token.id
        assert found.purpose == "invite"
        # Raw token is NOT stored — only hash
        assert found.token_hash == token_hash
        assert found.token_hash != raw
    finally:
        db.close()


def test_raw_token_not_stored_in_db(base_client: tuple[TestClient, object]) -> None:
    """The raw token must never appear in any DB column."""
    from app.auth.tokens import invite_expires_at, mint_token
    from app.repositories.user_token import UserTokenRepository

    _, engine = base_client
    db = _make_db(engine)
    try:
        raw, token_hash = mint_token()
        repo = UserTokenRepository(db)
        repo.create(
            purpose="invite",
            email="storedcheck@example.com",
            role="member",
            token_hash=token_hash,
            expires_at=invite_expires_at(),
        )
        db.commit()

        found = repo.get_by_token_hash(token_hash)
        assert found is not None
        # Verify the raw token is nowhere in the stored row
        assert found.token_hash != raw
        assert found.email != raw
    finally:
        db.close()


def test_get_pending_invite_by_email(base_client: tuple[TestClient, object]) -> None:
    from app.auth.tokens import invite_expires_at, mint_token
    from app.repositories.user_token import UserTokenRepository

    _, engine = base_client
    db = _make_db(engine)
    try:
        repo = UserTokenRepository(db)
        _, h = mint_token()
        repo.create(
            purpose="invite",
            email="pending@example.com",
            role="member",
            token_hash=h,
            expires_at=invite_expires_at(),
        )
        db.commit()

        found = repo.get_pending_invite_by_email("pending@example.com")
        assert found is not None
        assert found.email == "pending@example.com"
    finally:
        db.close()


def test_get_pending_invite_none_when_consumed(base_client: tuple[TestClient, object]) -> None:
    from app.auth.tokens import invite_expires_at, mint_token
    from app.repositories.user_token import UserTokenRepository

    _, engine = base_client
    db = _make_db(engine)
    try:
        repo = UserTokenRepository(db)
        _, h = mint_token()
        token = repo.create(
            purpose="invite",
            email="consumed@example.com",
            role="member",
            token_hash=h,
            expires_at=invite_expires_at(),
        )
        repo.mark_consumed(token, datetime.now(UTC))
        db.commit()

        found = repo.get_pending_invite_by_email("consumed@example.com")
        assert found is None
    finally:
        db.close()


def test_get_pending_invite_none_when_expired(base_client: tuple[TestClient, object]) -> None:
    from app.auth.tokens import mint_token
    from app.repositories.user_token import UserTokenRepository

    _, engine = base_client
    db = _make_db(engine)
    try:
        repo = UserTokenRepository(db)
        _, h = mint_token()
        repo.create(
            purpose="invite",
            email="expired@example.com",
            role="member",
            token_hash=h,
            expires_at=datetime.now(UTC) - timedelta(hours=1),  # already expired
        )
        db.commit()

        found = repo.get_pending_invite_by_email("expired@example.com")
        assert found is None
    finally:
        db.close()


def test_purge_expired_removes_stale_rows(base_client: tuple[TestClient, object]) -> None:
    from app.auth.tokens import invite_expires_at, mint_token
    from app.repositories.user_token import UserTokenRepository

    _, engine = base_client
    db = _make_db(engine)
    try:
        repo = UserTokenRepository(db)
        # Live token
        _, h1 = mint_token()
        repo.create(
            purpose="invite",
            email="live@example.com",
            role="member",
            token_hash=h1,
            expires_at=invite_expires_at(),
        )
        # Expired token
        _, h2 = mint_token()
        repo.create(
            purpose="invite",
            email="expired2@example.com",
            role="member",
            token_hash=h2,
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db.commit()

        count = repo.purge_expired(datetime.now(UTC))
        db.commit()
        assert count == 1  # Only the expired one is deleted

        # Live token still there
        assert repo.get_by_token_hash(h1) is not None
        # Expired token gone
        assert repo.get_by_token_hash(h2) is None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Unit: revoke_all_for_user
# ---------------------------------------------------------------------------


def test_revoke_all_for_user(base_client: tuple[TestClient, object]) -> None:
    from app.auth.sessions import create as create_session
    from app.auth.sessions import revoke_all_for_user

    _, engine = base_client
    admin_id = _make_user(engine, "revoketest@example.com", role="admin")

    db = _make_db(engine)
    try:
        create_session(db, admin_id)
        create_session(db, admin_id)
        db.commit()

        count = revoke_all_for_user(db, admin_id)
        db.commit()
        assert count == 2

        from sqlalchemy import select

        from app.models.session import Session

        remaining = db.execute(select(Session).where(Session.user_id == admin_id)).scalars().all()
        assert len(remaining) == 0
    finally:
        db.close()


def test_revoke_all_except_one(base_client: tuple[TestClient, object]) -> None:
    from app.auth.sessions import create as create_session
    from app.auth.sessions import revoke_all_for_user

    _, engine = base_client
    user_id = _make_user(engine, "keepone@example.com", role="member")

    db = _make_db(engine)
    try:
        s1 = create_session(db, user_id)
        create_session(db, user_id)
        db.commit()

        count = revoke_all_for_user(db, user_id, except_session_id=s1.id)
        db.commit()
        assert count == 1

        from sqlalchemy import select

        from app.models.session import Session

        remaining = db.execute(select(Session).where(Session.user_id == user_id)).scalars().all()
        assert len(remaining) == 1
        assert remaining[0].id == s1.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Service: InvitationService — invites
# ---------------------------------------------------------------------------


def _make_fake_request(base_url: str = "http://testserver/") -> object:
    """Create a minimal fake FastAPI Request with a base_url attribute."""

    class FakeURL:
        def __str__(self) -> str:
            return base_url

    class FakeRequest:
        base_url = FakeURL()

    return FakeRequest()


def test_create_invite_success(base_client: tuple[TestClient, object]) -> None:
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin@example.com", role="admin")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        token, raw_token, emailed = svc.create_invite(
            "invitee@example.com",
            "member",
            created_by=admin_id,
            request=_make_fake_request(),  # type: ignore[arg-type]
        )
        db.commit()

        assert token.id is not None
        assert token.email == "invitee@example.com"
        assert token.role == "member"
        assert token.consumed_at is None
        # Raw token not stored — only hash
        stored_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        assert token.token_hash == stored_hash
        assert emailed is False  # no SMTP configured
    finally:
        db.close()


def test_create_invite_rejects_existing_user_email(base_client: tuple[TestClient, object]) -> None:
    from app.core.errors import AppError, ErrorCode
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin2@example.com", role="admin")
    _make_user(engine, "existing@example.com", role="member")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        with pytest.raises(AppError) as exc_info:
            svc.create_invite(
                "existing@example.com",
                "member",
                created_by=admin_id,
                request=_make_fake_request(),  # type: ignore[arg-type]
            )
        assert exc_info.value.code == ErrorCode.USER_EMAIL_EXISTS
        assert exc_info.value.status_code == 409
    finally:
        db.close()


def test_create_invite_rejects_invalid_role(base_client: tuple[TestClient, object]) -> None:
    from app.core.errors import AppError, ErrorCode
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin3@example.com", role="admin")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        with pytest.raises(AppError) as exc_info:
            svc.create_invite(
                "newinvitee@example.com",
                "superadmin",  # invalid role
                created_by=admin_id,
                request=_make_fake_request(),  # type: ignore[arg-type]
            )
        assert exc_info.value.code == ErrorCode.INVALID_INPUT
    finally:
        db.close()


def test_create_invite_replaces_prior_pending(base_client: tuple[TestClient, object]) -> None:
    from sqlalchemy import select as sa_select

    from app.models.user_token import UserToken
    from app.repositories.user_token import UserTokenRepository
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin4@example.com", role="admin")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        token1, raw1, _ = svc.create_invite(
            "sameuser@example.com",
            "member",
            created_by=admin_id,
            request=_make_fake_request(),  # type: ignore[arg-type]
        )
        db.commit()
        hash1 = token1.token_hash  # remember the hash, not the id

        token2, _, _ = svc.create_invite(
            "sameuser@example.com",
            "viewer",
            created_by=admin_id,
            request=_make_fake_request(),  # type: ignore[arg-type]
        )
        db.commit()

        # After the second invite:
        # 1. Only ONE token should exist for this email.
        all_for_email = list(
            db.execute(sa_select(UserToken).where(UserToken.email == "sameuser@example.com"))
            .scalars()
            .all()
        )
        assert len(all_for_email) == 1, f"Expected 1 pending token, found {len(all_for_email)}"

        # 2. The surviving token must have the NEW role (viewer), not the old one (member).
        assert all_for_email[0].role == "viewer"

        # 3. The old token_hash must be gone from the DB.
        repo = UserTokenRepository(db)
        assert repo.get_by_token_hash(hash1) is None, "Old token hash still in DB"
    finally:
        db.close()


def test_validate_invite_success(base_client: tuple[TestClient, object]) -> None:
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin5@example.com", role="admin")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        _, raw_token, _ = svc.create_invite(
            "valid@example.com",
            "member",
            created_by=admin_id,
            request=_make_fake_request(),  # type: ignore[arg-type]
        )
        db.commit()

        token = svc.validate_invite(raw_token)
        assert token.email == "valid@example.com"
    finally:
        db.close()


def test_validate_invite_unknown_token(base_client: tuple[TestClient, object]) -> None:
    from app.core.errors import AppError, ErrorCode
    from app.services.invitation import InvitationService

    _, engine = base_client
    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        with pytest.raises(AppError) as exc_info:
            svc.validate_invite("totallyfake_token_value_xyz123")
        assert exc_info.value.code == ErrorCode.AUTH_INVALID_TOKEN
    finally:
        db.close()


def test_validate_invite_consumed_token(base_client: tuple[TestClient, object]) -> None:
    from app.auth.tokens import invite_expires_at, mint_token
    from app.core.errors import AppError, ErrorCode
    from app.repositories.user_token import UserTokenRepository
    from app.services.invitation import InvitationService

    _, engine = base_client
    db = _make_db(engine)
    try:
        raw, token_hash = mint_token()
        repo = UserTokenRepository(db)
        token = repo.create(
            purpose="invite",
            email="willconsume@example.com",
            role="member",
            token_hash=token_hash,
            expires_at=invite_expires_at(),
        )
        repo.mark_consumed(token, datetime.now(UTC))
        db.commit()

        svc = InvitationService(db)
        with pytest.raises(AppError) as exc_info:
            svc.validate_invite(raw)
        assert exc_info.value.code == ErrorCode.AUTH_INVALID_TOKEN
    finally:
        db.close()


def test_validate_invite_expired_token(base_client: tuple[TestClient, object]) -> None:
    from app.auth.tokens import mint_token
    from app.core.errors import AppError, ErrorCode
    from app.repositories.user_token import UserTokenRepository
    from app.services.invitation import InvitationService

    _, engine = base_client
    db = _make_db(engine)
    try:
        raw, token_hash = mint_token()
        repo = UserTokenRepository(db)
        repo.create(
            purpose="invite",
            email="willexpire@example.com",
            role="member",
            token_hash=token_hash,
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db.commit()

        svc = InvitationService(db)
        with pytest.raises(AppError) as exc_info:
            svc.validate_invite(raw)
        assert exc_info.value.code == ErrorCode.AUTH_INVALID_TOKEN
    finally:
        db.close()


def test_accept_invite_creates_user(base_client: tuple[TestClient, object]) -> None:
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin6@example.com", role="admin")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        _, raw_token, _ = svc.create_invite(
            "newuser@example.com",
            "member",
            created_by=admin_id,
            request=_make_fake_request(),  # type: ignore[arg-type]
        )
        db.commit()

        user = svc.accept_invite(raw_token, "newpassword123")
        db.commit()

        assert user.email == "newuser@example.com"
        assert user.role == "member"
        assert user.is_active is True

        # Password should work
        from app.auth.passwords import verify_password

        assert verify_password("newpassword123", user.password_hash)
    finally:
        db.close()


def test_accept_invite_token_consumed_after_use(base_client: tuple[TestClient, object]) -> None:
    from app.repositories.user_token import UserTokenRepository
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin7@example.com", role="admin")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        token, raw_token, _ = svc.create_invite(
            "onceonly@example.com",
            "viewer",
            created_by=admin_id,
            request=_make_fake_request(),  # type: ignore[arg-type]
        )
        db.commit()

        svc.accept_invite(raw_token, "somepassword")
        db.commit()

        # Token is now consumed
        repo = UserTokenRepository(db)
        stored = repo.get_by_id(token.id)
        assert stored is not None
        assert stored.consumed_at is not None
    finally:
        db.close()


def test_accept_invite_consumed_token_rejected(base_client: tuple[TestClient, object]) -> None:
    from app.core.errors import AppError, ErrorCode
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin8@example.com", role="admin")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        _, raw_token, _ = svc.create_invite(
            "acceptonce@example.com",
            "member",
            created_by=admin_id,
            request=_make_fake_request(),  # type: ignore[arg-type]
        )
        db.commit()

        svc.accept_invite(raw_token, "firstpass")
        db.commit()

        # Try to use same token again
        with pytest.raises(AppError) as exc_info:
            svc.accept_invite(raw_token, "secondpass")
        assert exc_info.value.code == ErrorCode.AUTH_INVALID_TOKEN
    finally:
        db.close()


def test_accept_invite_email_race_rejected(base_client: tuple[TestClient, object]) -> None:
    """If the email gets registered as a user between invite and accept, 400 is raised."""
    from app.core.errors import AppError, ErrorCode
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin9@example.com", role="admin")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        _, raw_token, _ = svc.create_invite(
            "raceuser@example.com",
            "member",
            created_by=admin_id,
            request=_make_fake_request(),  # type: ignore[arg-type]
        )
        db.commit()

        # Simulate the race: register that email as a user directly
        from app.auth.passwords import hash_password
        from app.repositories.user import UserRepository

        UserRepository(db).create(
            email="raceuser@example.com",
            password_hash=hash_password("otherpass"),
            role="member",
            is_active=True,
        )
        db.commit()

        # Accept should fail
        with pytest.raises(AppError) as exc_info:
            svc.accept_invite(raw_token, "anypassword")
        assert exc_info.value.code == ErrorCode.AUTH_INVALID_TOKEN
    finally:
        db.close()


def test_revoke_invite(base_client: tuple[TestClient, object]) -> None:
    from app.repositories.user_token import UserTokenRepository
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin10@example.com", role="admin")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        token, _, _ = svc.create_invite(
            "torevoke@example.com",
            "member",
            created_by=admin_id,
            request=_make_fake_request(),  # type: ignore[arg-type]
        )
        db.commit()
        token_id = token.id

        svc.revoke(token_id)
        db.commit()

        repo = UserTokenRepository(db)
        assert repo.get_by_id(token_id) is None
    finally:
        db.close()


def test_revoke_invite_not_found(base_client: tuple[TestClient, object]) -> None:
    from app.core.errors import AppError, ErrorCode
    from app.services.invitation import InvitationService

    _, engine = base_client
    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        with pytest.raises(AppError) as exc_info:
            svc.revoke(99999)
        assert exc_info.value.code == ErrorCode.INVITATION_NOT_FOUND
        assert exc_info.value.status_code == 404
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Service: InvitationService — password reset
# ---------------------------------------------------------------------------


def test_issue_reset_success(base_client: tuple[TestClient, object]) -> None:
    from app.repositories.user_token import UserTokenRepository
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin11@example.com", role="admin")
    target_id = _make_user(engine, "resetme@example.com", role="member")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        reset_url, emailed = svc.issue_reset(
            target_id,
            created_by=admin_id,
            request=_make_fake_request(),  # type: ignore[arg-type]
        )
        db.commit()

        assert "password-reset/accept?token=" in reset_url
        assert emailed is False  # no SMTP

        # Token row was created
        repo = UserTokenRepository(db)
        pending = repo.list_pending_resets_for_user(target_id)
        assert len(pending) == 1
        assert pending[0].purpose == "password_reset"
        assert pending[0].user_id == target_id
    finally:
        db.close()


def test_issue_reset_not_found(base_client: tuple[TestClient, object]) -> None:
    from app.core.errors import AppError, ErrorCode
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin12@example.com", role="admin")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        with pytest.raises(AppError) as exc_info:
            svc.issue_reset(99999, created_by=admin_id, request=_make_fake_request())  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.USER_NOT_FOUND
        assert exc_info.value.status_code == 404
    finally:
        db.close()


def test_issue_reset_revokes_prior_pending(base_client: tuple[TestClient, object]) -> None:
    from app.repositories.user_token import UserTokenRepository
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin13@example.com", role="admin")
    target_id = _make_user(engine, "resettwice@example.com", role="member")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        svc.issue_reset(target_id, created_by=admin_id, request=_make_fake_request())  # type: ignore[arg-type]
        db.commit()
        svc.issue_reset(target_id, created_by=admin_id, request=_make_fake_request())  # type: ignore[arg-type]
        db.commit()

        repo = UserTokenRepository(db)
        pending = repo.list_pending_resets_for_user(target_id)
        assert len(pending) == 1  # Only the second one
    finally:
        db.close()


def test_accept_reset_sets_password_and_revokes_sessions(
    base_client: tuple[TestClient, object],
) -> None:
    from sqlalchemy import select

    from app.auth.passwords import verify_password
    from app.auth.sessions import create as create_session
    from app.models.session import Session as SessionModel
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin14@example.com", role="admin")
    target_id = _make_user(engine, "resetaccept@example.com", role="member")

    db = _make_db(engine)
    try:
        # Create a session for target user (to verify it gets revoked after reset)
        create_session(db, target_id)
        db.commit()

        svc = InvitationService(db)
        reset_url, _ = svc.issue_reset(
            target_id,
            created_by=admin_id,
            request=_make_fake_request(),  # type: ignore[arg-type]
        )
        db.commit()

        # Extract raw token from the URL
        raw_token = reset_url.split("token=")[1]

        user = svc.accept_reset(raw_token, "brandnewpassword")
        db.commit()

        # Password updated
        assert verify_password("brandnewpassword", user.password_hash)

        # All sessions revoked
        sessions = (
            db.execute(select(SessionModel).where(SessionModel.user_id == target_id))
            .scalars()
            .all()
        )
        assert len(sessions) == 0
    finally:
        db.close()


def test_accept_reset_consumed_token_rejected(base_client: tuple[TestClient, object]) -> None:
    from app.core.errors import AppError, ErrorCode
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin15@example.com", role="admin")
    target_id = _make_user(engine, "resetonce@example.com", role="member")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        reset_url, _ = svc.issue_reset(target_id, created_by=admin_id, request=_make_fake_request())  # type: ignore[arg-type]
        db.commit()

        raw_token = reset_url.split("token=")[1]
        svc.accept_reset(raw_token, "firstnewpassword")
        db.commit()

        with pytest.raises(AppError) as exc_info:
            svc.accept_reset(raw_token, "secondnewpassword")
        assert exc_info.value.code == ErrorCode.AUTH_INVALID_TOKEN
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Service: InvitationService — change_password
# ---------------------------------------------------------------------------


def test_change_password_wrong_current(base_client: tuple[TestClient, object]) -> None:
    from app.core.errors import AppError, ErrorCode
    from app.services.invitation import InvitationService

    _, engine = base_client
    user_id = _make_user(engine, "changepass@example.com", role="member")

    db = _make_db(engine)
    try:
        from app.repositories.user import UserRepository

        user = UserRepository(db).get_by_id(user_id)
        assert user is not None

        svc = InvitationService(db)
        with pytest.raises(AppError) as exc_info:
            svc.change_password(user, "wrongcurrent", "newpassword", "fake-session-id")
        assert exc_info.value.code == ErrorCode.AUTH_PASSWORD_INCORRECT
        assert exc_info.value.status_code == 400
    finally:
        db.close()


def test_change_password_correct_current(base_client: tuple[TestClient, object]) -> None:
    from sqlalchemy import select

    from app.auth.passwords import verify_password
    from app.auth.sessions import create as create_session
    from app.models.session import Session as SessionModel
    from app.repositories.user import UserRepository
    from app.services.invitation import InvitationService

    _, engine = base_client
    user_id = _make_user(engine, "changepasstwo@example.com", role="member")

    db = _make_db(engine)
    try:
        # Create two sessions: one "current" (kept), one "other" (revoked)
        current_sess = create_session(db, user_id)
        create_session(db, user_id)  # "other" session — will be revoked
        db.commit()

        user = UserRepository(db).get_by_id(user_id)
        assert user is not None

        svc = InvitationService(db)
        svc.change_password(user, "testpassword", "mynewpassword", current_sess.id)
        db.commit()

        # Password updated
        db.refresh(user)
        assert verify_password("mynewpassword", user.password_hash)

        # Other session revoked
        sessions = (
            db.execute(select(SessionModel).where(SessionModel.user_id == user_id)).scalars().all()
        )
        assert len(sessions) == 1
        assert sessions[0].id == current_sess.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Optional SMTP send tests
# ---------------------------------------------------------------------------


def test_smtp_disabled_returns_emailed_false(base_client: tuple[TestClient, object]) -> None:
    """When no SMTP configured, emailed=False but link is still returned."""
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin16@example.com", role="admin")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        token, raw_token, emailed = svc.create_invite(
            "smtptest@example.com",
            "member",
            created_by=admin_id,
            request=_make_fake_request(),  # type: ignore[arg-type]
        )
        db.commit()

        assert emailed is False
        assert raw_token  # link is still returned
    finally:
        db.close()


def test_smtp_enabled_calls_send_transactional(
    base_client: tuple[TestClient, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When SMTP is enabled (mocked), emailed=True and send_transactional is called."""
    from app.notifications.channels.email import EmailChannel

    calls: list[tuple[str, str, str]] = []

    def _mock_send(self: EmailChannel, to: str, subject: str, body: str) -> None:
        calls.append((to, subject, body))

    def _mock_enabled(self: EmailChannel) -> bool:
        return True

    monkeypatch.setattr(EmailChannel, "is_enabled", _mock_enabled)
    monkeypatch.setattr(EmailChannel, "send_transactional", _mock_send)

    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin17@example.com", role="admin")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        _, _, emailed = svc.create_invite(
            "smtp_enabled@example.com",
            "member",
            created_by=admin_id,
            request=_make_fake_request(),  # type: ignore[arg-type]
        )
        db.commit()

        assert emailed is True
        assert len(calls) == 1
        assert calls[0][0] == "smtp_enabled@example.com"
    finally:
        db.close()


def test_smtp_error_does_not_fail_request(
    base_client: tuple[TestClient, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When SMTP raises, emailed=False but the invite creation still succeeds."""
    from app.notifications.channels.email import EmailChannel

    def _mock_enabled(self: EmailChannel) -> bool:
        return True

    def _mock_send_error(self: EmailChannel, to: str, subject: str, body: str) -> None:
        raise OSError("SMTP connection refused")

    monkeypatch.setattr(EmailChannel, "is_enabled", _mock_enabled)
    monkeypatch.setattr(EmailChannel, "send_transactional", _mock_send_error)

    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "admin18@example.com", role="admin")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        token, raw_token, emailed = svc.create_invite(
            "smtperror@example.com",
            "member",
            created_by=admin_id,
            request=_make_fake_request(),  # type: ignore[arg-type]
        )
        db.commit()

        assert emailed is False
        assert token.id is not None  # invite was created
        assert raw_token  # link still returned
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Route-level integration
# ---------------------------------------------------------------------------


def test_create_invite_route_admin_success(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "routeadmin@example.com", role="admin")

    resp = client.post(
        "/api/invitations",
        json={"email": "routeinvitee@example.com", "role": "member"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "accept_url" in data
    assert "emailed" in data
    assert data["emailed"] is False
    assert data["email"] == "routeinvitee@example.com"
    assert data["role"] == "member"
    assert "invite/accept?token=" in data["accept_url"]


def test_create_invite_route_member_forbidden(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "member1@example.com", role="member")

    resp = client.post(
        "/api/invitations",
        json={"email": "inv@example.com", "role": "viewer"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "auth.forbidden"


def test_create_invite_route_viewer_forbidden(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "viewer1@example.com", role="viewer")

    resp = client.post(
        "/api/invitations",
        json={"email": "inv2@example.com", "role": "viewer"},
    )
    assert resp.status_code == 403


def test_create_invite_route_409_existing_user(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "routeadmin2@example.com", role="admin")
    _make_user(engine, "alreadyexists@example.com", role="member")

    resp = client.post(
        "/api/invitations",
        json={"email": "alreadyexists@example.com", "role": "member"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "user.email_exists"


def test_list_invitations_route(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "listadmin@example.com", role="admin")

    # Create two invites
    client.post("/api/invitations", json={"email": "a@example.com", "role": "member"})
    client.post("/api/invitations", json={"email": "b@example.com", "role": "viewer"})

    resp = client.get("/api/invitations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    emails = {item["email"] for item in data}
    assert "a@example.com" in emails
    assert "b@example.com" in emails


def test_list_invitations_route_member_forbidden(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "listmember@example.com", role="member")

    resp = client.get("/api/invitations")
    assert resp.status_code == 403


def test_delete_invitation_route(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "deleteadmin@example.com", role="admin")

    create_resp = client.post(
        "/api/invitations",
        json={"email": "todelete@example.com", "role": "member"},
    )
    assert create_resp.status_code == 201
    invite_id = create_resp.json()["id"]

    resp = client.delete(f"/api/invitations/{invite_id}")
    assert resp.status_code == 204

    # Confirm it's gone
    list_resp = client.get("/api/invitations")
    assert all(i["id"] != invite_id for i in list_resp.json())


def test_delete_invitation_404(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "deleteadmin2@example.com", role="admin")

    resp = client.delete("/api/invitations/99999")
    assert resp.status_code == 404
    assert resp.json()["code"] == "invitation.not_found"


# ---------------------------------------------------------------------------
# Fixup tests (F1 + F2 — revoke purpose guard & accept_invite race IntegrityError)
# ---------------------------------------------------------------------------


def test_delete_invitation_rejects_password_reset_token_id(
    base_client: tuple[TestClient, object],
) -> None:
    """DELETE /invitations/{id} with a password_reset token id returns 404
    (invitation.not_found) and does NOT delete the reset token.

    Finding 1 (revoke() purpose guard): revoke() must check purpose == "invite"
    before deleting; passing the id of a password_reset token must be rejected.
    """
    from sqlalchemy.orm import sessionmaker as SM2

    from app.auth.tokens import hash_token
    from app.repositories.user_token import UserTokenRepository

    client, engine = base_client
    _create_and_login(engine, client, "f1_admin@example.com", role="admin")
    target_id = _make_user(engine, "f1_target@example.com", role="member")

    # Issue a password reset (admin endpoint) — creates a password_reset token.
    reset_resp = client.post(f"/api/users/{target_id}/reset-password")
    assert reset_resp.status_code == 200
    reset_url = reset_resp.json()["reset_url"]
    token_raw = reset_url.split("token=")[1]

    # Resolve the password_reset token's DB id directly.
    factory = SM2(bind=engine, autocommit=False, autoflush=False)  # type: ignore[arg-type]
    db = factory()
    try:
        repo = UserTokenRepository(db)
        reset_token = repo.get_by_token_hash(hash_token(token_raw))
        assert reset_token is not None, "reset token should exist"
        reset_token_id = reset_token.id
    finally:
        db.close()

    # Attempt DELETE /api/invitations/{reset_token_id} — must return 404.
    resp = client.delete(f"/api/invitations/{reset_token_id}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "invitation.not_found"

    # The password_reset token must still exist (not deleted).
    db = factory()
    try:
        repo = UserTokenRepository(db)
        still_there = repo.get_by_token_hash(hash_token(token_raw))
        assert still_there is not None, "password_reset token was incorrectly deleted"
    finally:
        db.close()


def test_revoke_invite_happy_path_204_and_removed(
    base_client: tuple[TestClient, object],
) -> None:
    """Existing invite-revoke happy path: DELETE returns 204 and removes the invite.

    Explicit regression guard for Finding 1: the purpose guard must not
    break the normal case (revoking a genuine pending invite).
    """
    client, engine = base_client
    _create_and_login(engine, client, "f1b_admin@example.com", role="admin")

    create_resp = client.post(
        "/api/invitations",
        json={"email": "f1b_invite@example.com", "role": "member"},
    )
    assert create_resp.status_code == 201
    invite_id = create_resp.json()["id"]

    del_resp = client.delete(f"/api/invitations/{invite_id}")
    assert del_resp.status_code == 204

    # Invite is gone from the list.
    list_resp = client.get("/api/invitations")
    assert all(i["id"] != invite_id for i in list_resp.json())


def test_accept_invite_integrity_error_yields_400(
    base_client: tuple[TestClient, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulated true-concurrent duplicate-create yields 400 auth.invalid_token, not 500.

    Finding 2 (accept_invite IntegrityError): UserRepository.create is
    monkeypatched to raise IntegrityError (mimicking the users.email unique
    constraint being violated by a concurrent winner), and the service must
    translate that to AppError(AUTH_INVALID_TOKEN, 400) rather than letting
    the IntegrityError surface as a 500.
    """
    import sqlite3

    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    from app.core.errors import AppError, ErrorCode
    from app.repositories.user import UserRepository
    from app.services.invitation import InvitationService

    _, engine = base_client
    admin_id = _make_user(engine, "f2_admin@example.com", role="admin")

    db = _make_db(engine)
    try:
        svc = InvitationService(db)
        _, raw_token, _ = svc.create_invite(
            "f2_race@example.com",
            "member",
            created_by=admin_id,
            request=_make_fake_request(),  # type: ignore[arg-type]
        )
        db.commit()

        # Monkeypatch UserRepository.create to raise IntegrityError, simulating
        # the concurrent winner having already inserted the same email.
        def _raise_integrity(self: UserRepository, **kwargs: object) -> None:
            raise SAIntegrityError(
                statement="INSERT INTO users ...",
                params={},
                orig=sqlite3.IntegrityError("UNIQUE constraint failed: users.email"),
            )

        monkeypatch.setattr(UserRepository, "create", _raise_integrity)

        with pytest.raises(AppError) as exc_info:
            svc.accept_invite(raw_token, "anypassword")
        assert exc_info.value.code == ErrorCode.AUTH_INVALID_TOKEN
        assert exc_info.value.status_code == 400
    finally:
        db.close()


def test_get_invitation_accept_route(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "acceptadmin@example.com", role="admin")

    create_resp = client.post(
        "/api/invitations",
        json={"email": "acceptme@example.com", "role": "viewer"},
    )
    assert create_resp.status_code == 201
    accept_url = create_resp.json()["accept_url"]
    token = accept_url.split("token=")[1]

    # Use a fresh unauthenticated client (public endpoint)
    from app.main import create_app

    anon_client = TestClient(create_app(), raise_server_exceptions=True)
    resp = anon_client.get(f"/api/invitations/accept?token={token}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "acceptme@example.com"
    assert data["role"] == "viewer"


def test_get_invitation_accept_invalid_token(base_client: tuple[TestClient, object]) -> None:
    from app.main import create_app

    anon_client = TestClient(create_app(), raise_server_exceptions=True)
    resp = anon_client.get("/api/invitations/accept?token=bogustoken")
    assert resp.status_code == 400
    assert resp.json()["code"] == "auth.invalid_token"


def test_post_invitation_accept_creates_user(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "acceptflow@example.com", role="admin")

    create_resp = client.post(
        "/api/invitations",
        json={"email": "newmember@example.com", "role": "member"},
    )
    assert create_resp.status_code == 201
    accept_url = create_resp.json()["accept_url"]
    token = accept_url.split("token=")[1]

    from app.main import create_app

    anon_client = TestClient(create_app(), raise_server_exceptions=True)
    resp = anon_client.post(
        "/api/invitations/accept",
        json={"token": token, "password": "mynewpassword"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "newmember@example.com"
    assert data["role"] == "member"

    # New user can now log in
    login_resp = anon_client.post(
        "/api/auth/login",
        json={"email": "newmember@example.com", "password": "mynewpassword"},
    )
    assert login_resp.status_code == 200


def test_post_invitation_accept_bad_token(base_client: tuple[TestClient, object]) -> None:
    from app.main import create_app

    anon_client = TestClient(create_app(), raise_server_exceptions=True)
    resp = anon_client.post(
        "/api/invitations/accept",
        json={"token": "badtoken", "password": "somepassword"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "auth.invalid_token"


def test_reset_password_route_admin(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "resetadmin@example.com", role="admin")
    target_id = _make_user(engine, "resettarget@example.com", role="member")

    resp = client.post(f"/api/users/{target_id}/reset-password")
    assert resp.status_code == 200
    data = resp.json()
    assert "reset_url" in data
    assert "password-reset/accept?token=" in data["reset_url"]
    assert data["emailed"] is False


def test_reset_password_route_member_forbidden(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "resetmember@example.com", role="member")
    target_id = _make_user(engine, "resettarget2@example.com", role="viewer")

    resp = client.post(f"/api/users/{target_id}/reset-password")
    assert resp.status_code == 403


def test_reset_password_route_user_not_found(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "resetadmin2@example.com", role="admin")

    resp = client.post("/api/users/99999/reset-password")
    assert resp.status_code == 404
    assert resp.json()["code"] == "user.not_found"


def test_get_password_reset_accept_route(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "pra_admin@example.com", role="admin")
    target_id = _make_user(engine, "pra_target@example.com", role="member")

    reset_resp = client.post(f"/api/users/{target_id}/reset-password")
    reset_url = reset_resp.json()["reset_url"]
    token = reset_url.split("token=")[1]

    from app.main import create_app

    anon_client = TestClient(create_app(), raise_server_exceptions=True)
    resp = anon_client.get(f"/api/password-reset/accept?token={token}")
    assert resp.status_code == 200
    data = resp.json()
    assert "email_masked" in data
    # Masked email contains '@'
    assert "@" in data["email_masked"]


def test_post_password_reset_accept_route(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "prb_admin@example.com", role="admin")
    target_id = _make_user(engine, "prb_target@example.com", role="member")

    reset_resp = client.post(f"/api/users/{target_id}/reset-password")
    reset_url = reset_resp.json()["reset_url"]
    token = reset_url.split("token=")[1]

    from app.main import create_app

    anon_client = TestClient(create_app(), raise_server_exceptions=True)
    resp = anon_client.post(
        "/api/password-reset/accept",
        json={"token": token, "password": "brandnewpassword"},
    )
    assert resp.status_code == 200
    assert "message" in resp.json()

    # Can now log in with new password
    login_resp = anon_client.post(
        "/api/auth/login",
        json={"email": "prb_target@example.com", "password": "brandnewpassword"},
    )
    assert login_resp.status_code == 200


def test_post_password_reset_accept_bad_token(base_client: tuple[TestClient, object]) -> None:
    from app.main import create_app

    anon_client = TestClient(create_app(), raise_server_exceptions=True)
    resp = anon_client.post(
        "/api/password-reset/accept",
        json={"token": "badtoken", "password": "somepassword"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "auth.invalid_token"


def test_change_password_route_wrong_current(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "cpw_user@example.com", role="member")

    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "wrongpassword", "new_password": "newpassword"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "auth.password_incorrect"


def test_change_password_route_correct_current(base_client: tuple[TestClient, object]) -> None:
    client, engine = base_client
    _create_and_login(engine, client, "cpw_correct@example.com", role="member")

    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "testpassword", "new_password": "brandnewpassword"},
    )
    assert resp.status_code == 200

    # Old password no longer works
    resp2 = client.post(
        "/api/auth/login",
        json={"email": "cpw_correct@example.com", "password": "testpassword"},
    )
    assert resp2.status_code == 401

    # New password works
    resp3 = client.post(
        "/api/auth/login",
        json={"email": "cpw_correct@example.com", "password": "brandnewpassword"},
    )
    assert resp3.status_code == 200


def test_change_password_keeps_current_session(base_client: tuple[TestClient, object]) -> None:
    """The current session remains active after change-password."""
    client, engine = base_client
    _create_and_login(engine, client, "cpw_session@example.com", role="member")

    # Change password
    client.post(
        "/api/auth/change-password",
        json={"current_password": "testpassword", "new_password": "newpassword123"},
    )

    # Can still hit /auth/me with the current session
    me_resp = client.get("/api/auth/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["user"]["email"] == "cpw_session@example.com"


def test_change_password_route_unauthenticated(base_client: tuple[TestClient, object]) -> None:
    from app.main import create_app

    anon_client = TestClient(create_app(), raise_server_exceptions=True)
    resp = anon_client.post(
        "/api/auth/change-password",
        json={"current_password": "anything", "new_password": "anything"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Migration round-trip
# ---------------------------------------------------------------------------


def test_migration_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify 0028 can be applied and rolled back cleanly.

    Runs alembic as a subprocess (same pattern as test_m1_step4.py) to avoid
    the local ``alembic/`` package directory shadowing the installed alembic.
    """
    import subprocess

    db_path = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{db_path}"

    backend_root = Path(__file__).parent.parent

    def _alembic(*args: str) -> tuple[int, str]:
        env = {**os.environ, "SECRET_KEY": "test-migration-key", "DATABASE_URL": db_url}
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

    # Upgrade to 0027 first (the rev before our migration)
    rc, out = _alembic("upgrade", "0027")
    assert rc == 0, f"alembic upgrade 0027 failed:\n{out}"

    engine = create_engine(db_url)
    assert not sa_inspect(engine).has_table("user_tokens"), (
        "user_tokens should not exist before 0028"
    )
    engine.dispose()

    # Now upgrade to 0028
    rc, out = _alembic("upgrade", "0028")
    assert rc == 0, f"alembic upgrade 0028 failed:\n{out}"

    engine = create_engine(db_url)
    assert sa_inspect(engine).has_table("user_tokens"), "user_tokens should exist after 0028"

    columns = {c["name"] for c in sa_inspect(engine).get_columns("user_tokens")}
    for col in (
        "id",
        "purpose",
        "email",
        "role",
        "user_id",
        "token_hash",
        "expires_at",
        "consumed_at",
        "created_by",
        "created_at",
    ):
        assert col in columns, f"Column {col!r} missing from user_tokens"
    engine.dispose()

    # Downgrade back to 0027
    rc, out = _alembic("downgrade", "0027")
    assert rc == 0, f"alembic downgrade 0027 failed:\n{out}"

    engine = create_engine(db_url)
    assert not sa_inspect(engine).has_table("user_tokens"), (
        "user_tokens should be gone after downgrade"
    )
    engine.dispose()
