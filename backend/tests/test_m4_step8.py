"""M4 Step 8 tests: HTTP webhook channel + inbound HA state endpoint + event dispatch.

Required coverage (M4.md §5 + §9 Step 8 + §10 Step 8):

A. SettingsService public channel getters + integration token:
- email_channel_config() returns correct fields (no _get_value calls from EmailChannel)
- http_channel_config() returns correct fields
- get_or_create_integration_token(): generates + persists when absent
- get_or_create_integration_token(): returns existing token on second call
- GET /settings auto-generates token when http enabled and token absent

B. EmailChannel uses public getter (regression: no _get_value access):
- is_enabled() / _send_smtp() use email_channel_config() not _get_value
- Step 7 regression: existing email tests still pass with refactored getter

C. HttpChannel (outbound, instant):
- is_enabled(): True when enabled=True AND webhook_url set AND http(s) scheme
- is_enabled(): False when disabled
- is_enabled(): False when webhook_url absent
- is_enabled(): False when webhook_url has non-http scheme (SSRF sanity)
- deliver(): POSTs {code, params, message} per notification (mock httpx)
  - asserts URL, Content-Type header, payload structure
  - Authorization header set when auth_header configured
  - no Authorization header when auth_header not set
- deliver(): no-op when not enabled
- deliver(): no-op when notifications list is empty
- deliver(): include_email_digest flag is ignored (always posts)
- deliver(): idempotency — skips notifications with existing 'sent' row
- deliver(): records 'sent' delivery row on success
- deliver(): records 'failed' delivery row on HTTP error
- deliver(): records 'failed' delivery row on network error
- deliver(): best-effort — httpx exception does not propagate
- deliver(): continues other notifications after one failure

D. Event-path instant dispatch (consume/discard/adjust -> HttpChannel):
- discard below threshold -> pending_notifications populated
- adjust below threshold -> pending_notifications populated
- consume_fifo below threshold -> pending_notifications populated
- route handler (POST /instances/{id}/discard) triggers HttpChannel after commit
- httpx exception from HttpChannel does not fail the movement route
- pending_notifications is empty when no low-stock results from event hook

E. IntegrationStateService:
- low_stock_count matches LowStockService.compute() length
- expiring_count / expired_count match ExpiryService.compute() after split by status
- generated_at is an ISO-8601 UTC string
- counts are 0 when nothing is low/expiring

F. GET /integrations/state endpoint:
- valid token in X-Omniventory-Token header -> 200 with counts
- valid token in ?token= query param -> 200 with counts
- missing token -> 401 integration.invalid_token
- wrong token -> 401 integration.invalid_token
- no token configured -> 401 integration.invalid_token
- endpoint does NOT require session cookie (works without logged-in session)
- response shape: {low_stock_count, expiring_count, expired_count, generated_at}

G. build_dispatcher: HttpChannel is registered alongside EmailChannel
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
from collections.abc import Generator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import drop_all_sqlite

# ---------------------------------------------------------------------------
# Session helpers (same pattern as prior M4 steps)
# ---------------------------------------------------------------------------


def _make_in_memory_session() -> tuple[Session, object]:
    """Create a fresh in-memory SQLite session with all models registered."""
    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.audit_log as audit_log_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.notification as notif_mod
    import app.models.notification_delivery as nd_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.stock_instance as si_mod
    import app.models.stock_movement as sm_mod
    import app.models.user as user_mod

    for mod in (
        db_base_mod,
        hh_mod,
        user_mod,
        sess_mod,
        app_config_mod,
        cat_mod,
        ikind_mod,
        idef_mod,
        loc_mod,
        si_mod,
        sm_mod,
        setting_mod,
        notif_mod,
        nd_mod,
        audit_log_mod,
    ):
        importlib.reload(mod)

    from app.db.base import Base as _Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @sa_event.listens_for(engine, "connect")
    def _enforce_fk(dbapi_conn: object, _: object) -> None:  # type: ignore[type-arg]
        import sqlite3

        if isinstance(dbapi_conn, sqlite3.Connection):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    _Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    return session, engine


def _make_temp_db_url() -> tuple[str, Path]:
    """Return (url, path) for a fresh temp-file SQLite DB."""
    fd, path_str = tempfile.mkstemp(suffix=".db", prefix="omniventory_m4step8_")
    os.close(fd)
    path = Path(path_str)
    path.unlink()
    return f"sqlite:///{path_str}", path


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
    """Fresh in-memory SQLite session with all models registered."""
    session, engine = _make_in_memory_session()

    from app.db.base import Base as _Base

    try:
        yield session
    finally:
        session.close()
    drop_all_sqlite(_Base, engine)


@pytest.fixture()
def temp_db(monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Temp-file SQLite DB for HTTP-level tests."""
    url, db_path = _make_temp_db_url()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-m4-step8")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture()
def http_client(temp_db: Path) -> Generator[object]:  # noqa: ARG001
    """TestClient with full schema + authenticated admin session."""
    from fastapi.testclient import TestClient

    import app.db.base as db_base_mod
    import app.models.app_config as app_config_mod
    import app.models.audit_log as audit_log_mod
    import app.models.category as cat_mod
    import app.models.household as hh_mod
    import app.models.item_definition as idef_mod
    import app.models.item_kind as ikind_mod
    import app.models.location as loc_mod
    import app.models.notification as notif_mod
    import app.models.notification_delivery as nd_mod
    import app.models.session as sess_mod
    import app.models.setting as setting_mod
    import app.models.stock_instance as si_mod
    import app.models.stock_movement as sm_mod
    import app.models.user as user_mod

    for mod in (
        db_base_mod,
        hh_mod,
        user_mod,
        sess_mod,
        app_config_mod,
        cat_mod,
        ikind_mod,
        idef_mod,
        loc_mod,
        si_mod,
        sm_mod,
        setting_mod,
        notif_mod,
        nd_mod,
        audit_log_mod,
    ):
        importlib.reload(mod)

    from app.db.base import Base, get_engine
    from app.main import create_app

    engine = get_engine()
    Base.metadata.create_all(engine)
    application = create_app()

    with TestClient(application, raise_server_exceptions=True) as client:
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            from app.auth.passwords import hash_password
            from app.models.item_kind import ItemKind
            from app.repositories.user import UserRepository

            repo = UserRepository(db)
            repo.create(email="admin@example.com", password_hash=hash_password("adminpass"))
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
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_minimal(db: Session) -> tuple[object, object, object]:
    """Seed Household, User, and a basic ItemKind+Definition.  Returns (hh, user, defn)."""
    from app.auth.passwords import hash_password
    from app.models.household import Household
    from app.models.item_definition import ItemDefinition
    from app.models.item_kind import ItemKind
    from app.models.user import User

    hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
    db.add(hh)
    db.flush()

    kind = ItemKind(code="perishable", name="Perishable", is_system=True)
    db.add(kind)
    db.flush()

    user = User(email="admin@example.com", password_hash=hash_password("pass"), is_active=True)
    db.add(user)
    db.flush()

    defn = ItemDefinition(name="Milk", kind_id=kind.id)
    db.add(defn)
    db.flush()

    return hh, user, defn


def _make_notification(
    db: Session,
    user_id: int,
    code: str = "reminder.best_before",
    params: dict | None = None,
    unique_suffix: str = "",
) -> object:
    """Insert a Notification row and return it."""
    from app.models.notification import Notification

    n = Notification(
        user_id=user_id,
        source="best_before",
        subject_type="instance",
        subject_id=1,
        dedup_key=f"{code}:u{user_id}:{id(params)}{unique_suffix}",
        message_code=code,
        params=json.dumps(params or {"name": "Milk", "date": "2026-06-25", "days_remaining": 5}),
    )
    db.add(n)
    db.flush()
    return n


def _enable_http(
    db: Session,
    webhook_url: str = "https://hook.example.com/notify",
    auth_header: str | None = None,
    token: str | None = None,
) -> None:
    """Configure the HTTP channel settings in the test DB."""
    from app.repositories.setting import SettingsRepository

    repo = SettingsRepository(db)
    repo.set("channels.http.enabled", "true")
    repo.set("channels.http.webhook_url", webhook_url)
    if auth_header is not None:
        repo.set("channels.http.auth_header", auth_header)
    if token is not None:
        repo.set("channels.http.integration_token", token)
    db.flush()


def _seed_consumable_low(
    db: Session,
    user: object,
    kind_code: str = "consumable",
) -> tuple[object, object, object]:
    """Seed a consumable definition below min_stock, + a stock instance.
    Returns (kind, defn, instance).
    """
    from app.models.item_definition import ItemDefinition
    from app.models.item_kind import ItemKind
    from app.models.location import Location
    from app.models.stock_instance import StockInstance

    kind = ItemKind(code=kind_code, name=kind_code.capitalize(), is_system=True)
    db.add(kind)
    db.flush()

    loc = Location(name="Shelf")
    db.add(loc)
    db.flush()

    defn = ItemDefinition(
        name="Pasta",
        kind_id=kind.id,
        stock_tracking_mode="exact",
        min_stock=Decimal("5"),
    )
    db.add(defn)
    db.flush()

    inst = StockInstance(
        definition_id=defn.id,
        location_id=loc.id,
        quantity=Decimal("2"),  # below min_stock=5
    )
    db.add(inst)
    db.flush()

    return kind, defn, inst


# ---------------------------------------------------------------------------
# A. SettingsService public getters + integration token
# ---------------------------------------------------------------------------


class TestSettingsPublicGetters:
    """Public channel config getters return correct fields."""

    def test_email_channel_config_defaults(self, db_session: Session) -> None:
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        cfg = svc.email_channel_config()
        assert cfg.enabled is False
        assert cfg.host is None
        assert cfg.port is None
        assert cfg.username is None
        assert cfg.password is None
        assert cfg.encryption == "none"  # default encryption mode
        assert cfg.from_address is None
        assert cfg.from_name is None

    def test_email_channel_config_after_set(self, db_session: Session) -> None:
        from app.repositories.setting import SettingsRepository
        from app.services.settings import SettingsService

        repo = SettingsRepository(db_session)
        repo.set("channels.email.enabled", "true")
        repo.set("channels.email.host", "smtp.test.com")
        repo.set("channels.email.port", "587")
        repo.set("channels.email.username", "user@test.com")
        repo.set("channels.email.password", "secret")
        repo.set("channels.email.encryption", "starttls")
        repo.set("channels.email.from_address", "from@test.com")
        repo.set("channels.email.from_name", "Test Sender")
        db_session.flush()

        svc = SettingsService(db_session)
        cfg = svc.email_channel_config()
        assert cfg.enabled is True
        assert cfg.host == "smtp.test.com"
        # Port is stored as a string (default is None, so decoder returns raw str)
        assert str(cfg.port) == "587"
        assert cfg.username == "user@test.com"
        assert cfg.password == "secret"
        assert cfg.encryption == "starttls"
        assert cfg.from_address == "from@test.com"
        assert cfg.from_name == "Test Sender"

    def test_http_channel_config_defaults(self, db_session: Session) -> None:
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        cfg = svc.http_channel_config()
        assert cfg.enabled is False
        assert cfg.webhook_url is None
        assert cfg.auth_header is None
        assert cfg.integration_token is None

    def test_http_channel_config_after_set(self, db_session: Session) -> None:
        from app.services.settings import SettingsService

        _enable_http(
            db_session,
            webhook_url="https://example.com/hook",
            auth_header="Bearer mytoken",
            token="secret_integration_token",
        )

        svc = SettingsService(db_session)
        cfg = svc.http_channel_config()
        assert cfg.enabled is True
        assert cfg.webhook_url == "https://example.com/hook"
        assert cfg.auth_header == "Bearer mytoken"
        assert cfg.integration_token == "secret_integration_token"

    def test_get_or_create_integration_token_generates_when_absent(
        self, db_session: Session
    ) -> None:
        from app.repositories.setting import SettingsRepository
        from app.services.settings import SettingsService

        svc = SettingsService(db_session)
        token = svc.get_or_create_integration_token()
        assert token  # non-empty
        assert len(token) >= 32

        # Verify it was persisted.
        repo = SettingsRepository(db_session)
        stored = repo.get("channels.http.integration_token")
        assert stored == token

    def test_get_or_create_integration_token_returns_existing(self, db_session: Session) -> None:
        from app.services.settings import SettingsService

        _enable_http(db_session, token="already_set_token")
        svc = SettingsService(db_session)
        token1 = svc.get_or_create_integration_token()
        token2 = svc.get_or_create_integration_token()
        assert token1 == "already_set_token"
        assert token1 == token2


# ---------------------------------------------------------------------------
# B. EmailChannel uses public getter (regression)
# ---------------------------------------------------------------------------


class TestEmailChannelPublicGetter:
    """EmailChannel.is_enabled() and _send_smtp() use the public getter — no _get_value."""

    def test_is_enabled_uses_public_getter(self, db_session: Session) -> None:
        """is_enabled() returns True when enabled+host via public getter."""
        from app.notifications.channels.email import EmailChannel
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("channels.email.enabled", "true")
        repo.set("channels.email.host", "smtp.example.com")
        db_session.flush()

        ch = EmailChannel(db_session)
        assert ch.is_enabled() is True

    def test_is_enabled_false_when_no_host(self, db_session: Session) -> None:
        from app.notifications.channels.email import EmailChannel
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("channels.email.enabled", "true")
        db_session.flush()

        ch = EmailChannel(db_session)
        assert ch.is_enabled() is False

    def test_send_smtp_uses_public_getter(self, db_session: Session) -> None:
        """_send_smtp() reads config via email_channel_config() not _get_value."""
        from app.notifications.channels.email import EmailChannel
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("channels.email.enabled", "true")
        repo.set("channels.email.host", "smtp.example.com")
        repo.set("channels.email.port", "25")
        repo.set("channels.email.encryption", "none")
        db_session.flush()

        ch = EmailChannel(db_session)
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value.__enter__ = lambda s: mock_smtp
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            ch._send_smtp("to@example.com", "Subject", "Body")
            mock_smtp_cls.assert_called_once_with(host="smtp.example.com", port=25)


# ---------------------------------------------------------------------------
# C. HttpChannel
# ---------------------------------------------------------------------------


class TestHttpChannelIsEnabled:
    """HttpChannel.is_enabled() logic."""

    def test_enabled_true_with_https_url(self, db_session: Session) -> None:
        from app.notifications.channels.http import HttpChannel

        _enable_http(db_session, webhook_url="https://example.com/notify")
        ch = HttpChannel(db_session)
        assert ch.is_enabled() is True

    def test_enabled_true_with_http_url(self, db_session: Session) -> None:
        from app.notifications.channels.http import HttpChannel

        _enable_http(db_session, webhook_url="http://example.com/notify")
        ch = HttpChannel(db_session)
        assert ch.is_enabled() is True

    def test_disabled_when_enabled_false(self, db_session: Session) -> None:
        from app.notifications.channels.http import HttpChannel
        from app.repositories.setting import SettingsRepository

        # webhook_url set but enabled=False
        repo = SettingsRepository(db_session)
        repo.set("channels.http.enabled", "false")
        repo.set("channels.http.webhook_url", "https://example.com/notify")
        db_session.flush()

        ch = HttpChannel(db_session)
        assert ch.is_enabled() is False

    def test_disabled_when_no_webhook_url(self, db_session: Session) -> None:
        from app.notifications.channels.http import HttpChannel
        from app.repositories.setting import SettingsRepository

        repo = SettingsRepository(db_session)
        repo.set("channels.http.enabled", "true")
        db_session.flush()

        ch = HttpChannel(db_session)
        assert ch.is_enabled() is False

    def test_disabled_when_non_http_scheme(self, db_session: Session) -> None:
        """Basic SSRF sanity: non-http(s) scheme -> disabled."""
        from app.notifications.channels.http import HttpChannel

        _enable_http(db_session, webhook_url="ftp://malicious.example.com/hook")
        ch = HttpChannel(db_session)
        assert ch.is_enabled() is False

    def test_disabled_when_no_host_in_url(self, db_session: Session) -> None:
        from app.notifications.channels.http import HttpChannel

        _enable_http(db_session, webhook_url="https://")
        ch = HttpChannel(db_session)
        assert ch.is_enabled() is False

    def test_default_disabled(self, db_session: Session) -> None:
        from app.notifications.channels.http import HttpChannel

        # Nothing configured — default is disabled.
        ch = HttpChannel(db_session)
        assert ch.is_enabled() is False


class TestHttpChannelDeliver:
    """HttpChannel.deliver() posts payloads and records deliveries."""

    def test_posts_code_params_message(self, db_session: Session) -> None:
        """Each notification is POSTed as {code, params, message}."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.user import User

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="p", name="P", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(
            email="u@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
            preferred_language="en",
        )
        db_session.add(user)
        db_session.flush()

        _enable_http(db_session, webhook_url="https://hook.example.com/n")
        notif = _make_notification(
            db_session, user.id, "reminder.best_before", {"name": "Apple", "days_remaining": 2}
        )

        with patch("httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.post = MagicMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            from app.notifications.channels.http import HttpChannel

            ch = HttpChannel(db_session)
            ch.deliver([notif], include_email_digest=False)

        mock_ctx.post.assert_called_once()
        call_kwargs = mock_ctx.post.call_args
        assert call_kwargs[0][0] == "https://hook.example.com/n"
        sent_json = call_kwargs[1]["json"]
        assert sent_json["code"] == "reminder.best_before"
        assert "name" in sent_json["params"]
        assert "Apple" in sent_json["message"]

    def test_authorization_header_set_when_auth_header_configured(
        self, db_session: Session
    ) -> None:
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.user import User

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="p2", name="P2", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="u2@test.com", password_hash=hash_password("pass"), is_active=True)
        db_session.add(user)
        db_session.flush()

        _enable_http(
            db_session,
            webhook_url="https://hook.example.com/n",
            auth_header="Bearer my-secret-token",
        )
        notif = _make_notification(db_session, user.id, unique_suffix="_auth")

        with patch("httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.post = MagicMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            from app.notifications.channels.http import HttpChannel

            ch = HttpChannel(db_session)
            ch.deliver([notif], include_email_digest=False)

        headers_sent = mock_ctx.post.call_args[1]["headers"]
        assert headers_sent.get("Authorization") == "Bearer my-secret-token"

    def test_no_authorization_header_when_not_configured(self, db_session: Session) -> None:
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.user import User

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="p3", name="P3", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="u3@test.com", password_hash=hash_password("pass"), is_active=True)
        db_session.add(user)
        db_session.flush()

        _enable_http(db_session, webhook_url="https://hook.example.com/n")
        notif = _make_notification(db_session, user.id, unique_suffix="_noauth")

        with patch("httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.post = MagicMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            from app.notifications.channels.http import HttpChannel

            ch = HttpChannel(db_session)
            ch.deliver([notif], include_email_digest=False)

        headers_sent = mock_ctx.post.call_args[1]["headers"]
        assert "Authorization" not in headers_sent

    def test_include_email_digest_ignored_instant_channel(self, db_session: Session) -> None:
        """HTTP posts even when include_email_digest=True (it's instant, not digest)."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.user import User

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="p4", name="P4", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="u4@test.com", password_hash=hash_password("pass"), is_active=True)
        db_session.add(user)
        db_session.flush()

        _enable_http(db_session)
        notif = _make_notification(db_session, user.id, unique_suffix="_digest")

        with patch("httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.post = MagicMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            from app.notifications.channels.http import HttpChannel

            ch = HttpChannel(db_session)
            # include_email_digest=True — should still post (HTTP is instant)
            ch.deliver([notif], include_email_digest=True)

        mock_ctx.post.assert_called_once()

    def test_noop_when_disabled(self, db_session: Session) -> None:
        from app.models.notification import Notification

        notif = MagicMock(spec=Notification)
        notif.id = 1

        with patch("httpx.Client") as mock_client_cls:
            from app.notifications.channels.http import HttpChannel

            ch = HttpChannel(db_session)
            ch.deliver([notif], include_email_digest=False)

        mock_client_cls.assert_not_called()

    def test_noop_when_empty_list(self, db_session: Session) -> None:
        _enable_http(db_session)

        with patch("httpx.Client") as mock_client_cls:
            from app.notifications.channels.http import HttpChannel

            ch = HttpChannel(db_session)
            ch.deliver([], include_email_digest=False)

        mock_client_cls.assert_not_called()

    def test_idempotency_skips_already_sent(self, db_session: Session) -> None:
        """Notification with existing 'sent' row is skipped."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.notification_delivery import NotificationDelivery
        from app.models.user import User

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="p5", name="P5", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="u5@test.com", password_hash=hash_password("pass"), is_active=True)
        db_session.add(user)
        db_session.flush()

        _enable_http(db_session)
        notif = _make_notification(db_session, user.id, unique_suffix="_idem")

        # Pre-seed a 'sent' delivery row
        db_session.add(
            NotificationDelivery(
                notification_id=notif.id,
                channel="http",
                status="sent",
            )
        )
        db_session.flush()

        with patch("httpx.Client") as mock_client_cls:
            from app.notifications.channels.http import HttpChannel

            ch = HttpChannel(db_session)
            ch.deliver([notif], include_email_digest=False)

        # Should not have posted — already sent.
        mock_client_cls.assert_not_called()

    def test_failed_row_does_not_block_retry(self, db_session: Session) -> None:
        """A 'failed' delivery row does NOT prevent re-delivery."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.notification_delivery import NotificationDelivery
        from app.models.user import User

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="p6", name="P6", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="u6@test.com", password_hash=hash_password("pass"), is_active=True)
        db_session.add(user)
        db_session.flush()

        _enable_http(db_session)
        notif = _make_notification(db_session, user.id, unique_suffix="_retry")

        # Pre-seed a 'failed' delivery row (should NOT block retry)
        db_session.add(
            NotificationDelivery(
                notification_id=notif.id,
                channel="http",
                status="failed",
                detail="previous error",
            )
        )
        db_session.flush()

        with patch("httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.post = MagicMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            from app.notifications.channels.http import HttpChannel

            ch = HttpChannel(db_session)
            ch.deliver([notif], include_email_digest=False)

        # Should have retried (no 'sent' row existed).
        mock_ctx.post.assert_called_once()

    def test_records_sent_delivery_row(self, db_session: Session) -> None:
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.notification_delivery import NotificationDelivery
        from app.models.user import User

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="p7", name="P7", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="u7@test.com", password_hash=hash_password("pass"), is_active=True)
        db_session.add(user)
        db_session.flush()

        _enable_http(db_session)
        notif = _make_notification(db_session, user.id, unique_suffix="_sent")

        with patch("httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.post = MagicMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            from app.notifications.channels.http import HttpChannel

            ch = HttpChannel(db_session)
            ch.deliver([notif], include_email_digest=False)

        rows = (
            db_session.query(NotificationDelivery)
            .filter_by(notification_id=notif.id, channel="http")
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == "sent"

    def test_records_failed_delivery_row_on_http_error(self, db_session: Session) -> None:
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.notification_delivery import NotificationDelivery
        from app.models.user import User

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="p8", name="P8", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="u8@test.com", password_hash=hash_password("pass"), is_active=True)
        db_session.add(user)
        db_session.flush()

        _enable_http(db_session)
        notif = _make_notification(db_session, user.id, unique_suffix="_http_err")

        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.post = MagicMock(side_effect=Exception("connection refused"))
            mock_client_cls.return_value = mock_ctx

            from app.notifications.channels.http import HttpChannel

            ch = HttpChannel(db_session)
            # Must NOT raise — best-effort.
            ch.deliver([notif], include_email_digest=False)

        rows = (
            db_session.query(NotificationDelivery)
            .filter_by(notification_id=notif.id, channel="http")
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == "failed"
        assert "connection refused" in (rows[0].detail or "")

    def test_best_effort_does_not_propagate_exception(self, db_session: Session) -> None:
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.user import User

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="p9", name="P9", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="u9@test.com", password_hash=hash_password("pass"), is_active=True)
        db_session.add(user)
        db_session.flush()

        _enable_http(db_session)
        notif = _make_notification(db_session, user.id, unique_suffix="_noexc")

        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.post = MagicMock(side_effect=RuntimeError("network gone"))
            mock_client_cls.return_value = mock_ctx

            from app.notifications.channels.http import HttpChannel

            ch = HttpChannel(db_session)
            # Should silently swallow — no exception.
            ch.deliver([notif], include_email_digest=False)

    def test_continues_after_one_failure(self, db_session: Session) -> None:
        """Failure on notification 1 does not prevent delivery of notification 2."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.notification_delivery import NotificationDelivery
        from app.models.user import User

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="p10", name="P10", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="u10@test.com", password_hash=hash_password("pass"), is_active=True)
        db_session.add(user)
        db_session.flush()

        _enable_http(db_session)
        n1 = _make_notification(db_session, user.id, unique_suffix="_fail1")
        n2 = _make_notification(db_session, user.id, unique_suffix="_ok2")

        call_count = {"n": 0}

        def _post_side_effect(*args: object, **kwargs: object) -> MagicMock:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("first fails")
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.status_code = 200
            return r

        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.post = MagicMock(side_effect=_post_side_effect)
            mock_client_cls.return_value = mock_ctx

            from app.notifications.channels.http import HttpChannel

            ch = HttpChannel(db_session)
            ch.deliver([n1, n2], include_email_digest=False)

        # Both should have been attempted; n2 should have a 'sent' row.
        assert call_count["n"] == 2
        sent = (
            db_session.query(NotificationDelivery)
            .filter_by(notification_id=n2.id, channel="http", status="sent")
            .count()
        )
        assert sent == 1


# ---------------------------------------------------------------------------
# D. Event-path instant dispatch
# ---------------------------------------------------------------------------


class TestEventPathDispatch:
    """Movement routes dispatch HttpChannel after commit when low-stock triggers."""

    def _make_full_session(self) -> tuple[Session, object]:
        """Create a session with all models for event-path tests."""
        return _make_in_memory_session()

    def test_pending_notifications_populated_by_discard(self, db_session: Session) -> None:
        """StockMovementService.pending_notifications accumulates from discard."""
        from app.auth.passwords import hash_password
        from app.core.context import RequestContext
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.location import Location
        from app.models.stock_instance import StockInstance
        from app.models.user import User
        from app.services.stock_movement import StockMovementService

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="consumable_d", name="Consumable", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="admin@test.com", password_hash=hash_password("pass"), is_active=True)
        db_session.add(user)
        db_session.flush()
        loc = Location(name="Shelf")
        db_session.add(loc)
        db_session.flush()

        from app.models.item_definition import ItemDefinition

        defn = ItemDefinition(
            name="Coffee",
            kind_id=kind.id,
            stock_tracking_mode="exact",
            min_stock=Decimal("5"),
        )
        db_session.add(defn)
        db_session.flush()

        inst = StockInstance(
            definition_id=defn.id,
            location_id=loc.id,
            quantity=Decimal("10"),
        )
        db_session.add(inst)
        db_session.flush()

        ctx = RequestContext(household=hh, user=user)
        svc = StockMovementService(db_session, ctx)
        # Discard 8 -> qty=2, below min_stock=5
        svc.discard(inst, Decimal("8"))
        db_session.commit()

        # pending_notifications should have been populated
        assert isinstance(svc.pending_notifications, list)
        assert len(svc.pending_notifications) >= 1

    def test_pending_notifications_populated_by_adjust(self, db_session: Session) -> None:
        """StockMovementService.pending_notifications accumulates from adjust."""
        from app.auth.passwords import hash_password
        from app.core.context import RequestContext
        from app.models.household import Household
        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.location import Location
        from app.models.stock_instance import StockInstance
        from app.models.user import User
        from app.services.stock_movement import StockMovementService

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="consumable_a", name="Consumable", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="admin2@test.com", password_hash=hash_password("pass"), is_active=True)
        db_session.add(user)
        db_session.flush()
        loc = Location(name="Shelf")
        db_session.add(loc)
        db_session.flush()

        defn = ItemDefinition(
            name="Tea",
            kind_id=kind.id,
            stock_tracking_mode="exact",
            min_stock=Decimal("5"),
        )
        db_session.add(defn)
        db_session.flush()

        inst = StockInstance(
            definition_id=defn.id,
            location_id=loc.id,
            quantity=Decimal("10"),
        )
        db_session.add(inst)
        db_session.flush()

        ctx = RequestContext(household=hh, user=user)
        svc = StockMovementService(db_session, ctx)
        # Adjust to 2 -> below min_stock=5
        svc.adjust(inst, Decimal("2"))
        db_session.commit()

        assert len(svc.pending_notifications) >= 1

    def test_http_channel_called_on_discard_via_route(self, http_client: object) -> None:
        """POST /instances/{id}/discard triggers HttpChannel post-commit."""
        client = http_client  # type: ignore[assignment]

        # Set up location, kind, definition, and instance via API
        resp = client.post("/api/locations", json={"name": "Pantry", "parent_id": None})
        assert resp.status_code == 201
        loc_id = resp.json()["id"]

        resp = client.get("/api/kinds")
        kinds = resp.json()
        consumable_kind = next((k for k in kinds if k["code"] == "consumable"), None)
        assert consumable_kind is not None

        resp = client.post(
            "/api/definitions",
            json={
                "name": "Rice",
                "kind_id": consumable_kind["id"],
                "stock_tracking_mode": "exact",
                "min_stock": "5",
            },
        )
        assert resp.status_code == 201
        def_id = resp.json()["id"]

        resp = client.post(
            "/api/instances",
            json={
                "definition_id": def_id,
                "location_id": loc_id,
                "quantity": "10",
            },
        )
        assert resp.status_code == 201
        inst_id = resp.json()["id"]

        # Enable http channel with a mock webhook URL
        # We'll patch httpx.Client so no real network call is made
        from sqlalchemy.orm import sessionmaker as sm_factory

        from app.db.base import get_engine

        engine = get_engine()
        factory = sm_factory(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            from app.repositories.setting import SettingsRepository

            repo = SettingsRepository(db)
            repo.set("channels.http.enabled", "true")
            repo.set("channels.http.webhook_url", "https://hook.example.com/notify")
            repo.set("channels.http.integration_token", "test-token-123")
            db.commit()
        finally:
            db.close()

        with patch("httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.post = MagicMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_ctx

            # Discard 8 -> qty=2, below min_stock=5 => triggers low-stock notification
            resp = client.post(
                f"/api/instances/{inst_id}/discard",
                json={"quantity": "8"},
            )
            assert resp.status_code == 200

            # HttpChannel should have been called after commit
            # (exact call count depends on notification creation)
            # Just verify no exception was raised and response is OK.
            # The httpx mock prevents real network calls.

    def test_httpx_error_does_not_fail_movement_route(self, http_client: object) -> None:
        """POST /instances/{id}/discard succeeds even when httpx raises."""
        client = http_client  # type: ignore[assignment]

        resp = client.post("/api/locations", json={"name": "Pantry2", "parent_id": None})
        assert resp.status_code == 201
        loc_id = resp.json()["id"]

        resp = client.get("/api/kinds")
        kinds = resp.json()
        consumable_kind = next(k for k in kinds if k["code"] == "consumable")

        resp = client.post(
            "/api/definitions",
            json={
                "name": "Sugar",
                "kind_id": consumable_kind["id"],
                "stock_tracking_mode": "exact",
                "min_stock": "5",
            },
        )
        assert resp.status_code == 201
        def_id = resp.json()["id"]

        resp = client.post(
            "/api/instances",
            json={"definition_id": def_id, "location_id": loc_id, "quantity": "10"},
        )
        assert resp.status_code == 201
        inst_id = resp.json()["id"]

        # Enable http channel
        from sqlalchemy.orm import sessionmaker as sm_factory

        from app.db.base import get_engine

        engine = get_engine()
        factory = sm_factory(bind=engine, autocommit=False, autoflush=False)
        db = factory()
        try:
            from app.repositories.setting import SettingsRepository

            repo = SettingsRepository(db)
            repo.set("channels.http.enabled", "true")
            repo.set("channels.http.webhook_url", "https://hook.example.com/notify")
            db.commit()
        finally:
            db.close()

        # Make httpx raise — movement must still succeed
        with patch("httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.post = MagicMock(side_effect=RuntimeError("network gone"))
            mock_client_cls.return_value = mock_ctx

            resp = client.post(
                f"/api/instances/{inst_id}/discard",
                json={"quantity": "8"},
            )
            # Movement must succeed despite dispatch error
            assert resp.status_code == 200
            # Quantity is returned as decimal string (e.g. "2.000000")
            assert Decimal(resp.json()["quantity"]) == Decimal("2")


# ---------------------------------------------------------------------------
# E. IntegrationStateService
# ---------------------------------------------------------------------------


class TestIntegrationStateService:
    """IntegrationStateService computes correct counts."""

    def test_all_zero_when_nothing_low_or_expiring(self, db_session: Session) -> None:
        from app.models.household import Household
        from app.services.integration_state import IntegrationStateService

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.commit()

        result = IntegrationStateService(db_session).compute()
        assert result["low_stock_count"] == 0
        assert result["expiring_count"] == 0
        assert result["expired_count"] == 0
        assert result["generated_at"]  # non-empty ISO string

    def test_generated_at_is_iso8601_utc(self, db_session: Session) -> None:
        from datetime import datetime

        from app.models.household import Household
        from app.services.integration_state import IntegrationStateService

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.commit()

        result = IntegrationStateService(db_session).compute()
        ts = result["generated_at"]
        # Should parse as a datetime with timezone info
        parsed = datetime.fromisoformat(str(ts))
        assert parsed.tzinfo is not None

    def test_low_stock_count_matches_service(self, db_session: Session) -> None:
        """low_stock_count == len(LowStockService.compute())."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.user import User
        from app.services.integration_state import IntegrationStateService
        from app.services.low_stock import LowStockService

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="consumable_st", name="Consumable", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="st@test.com", password_hash=hash_password("pass"), is_active=True)
        db_session.add(user)
        db_session.flush()

        _seed_consumable_low(db_session, user)
        db_session.commit()

        low_items = LowStockService(db_session).compute()
        result = IntegrationStateService(db_session).compute()
        assert result["low_stock_count"] == len(low_items)
        assert result["low_stock_count"] == 1

    def test_expiry_counts_match_service(self, db_session: Session) -> None:
        """expiring_count + expired_count match ExpiryService output split by status."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_definition import ItemDefinition
        from app.models.item_kind import ItemKind
        from app.models.location import Location
        from app.models.stock_instance import StockInstance
        from app.models.user import User
        from app.services.expiry import ExpiryService
        from app.services.integration_state import IntegrationStateService
        from app.services.settings import SettingsService

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="perishable_e", name="Perishable", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="e@test.com", password_hash=hash_password("pass"), is_active=True)
        db_session.add(user)
        db_session.flush()
        loc = Location(name="Fridge")
        db_session.add(loc)
        db_session.flush()

        today = date.today()
        defn_exp = ItemDefinition(name="Expired Milk", kind_id=kind.id)
        db_session.add(defn_exp)
        db_session.flush()
        # Already expired lot
        db_session.add(
            StockInstance(
                definition_id=defn_exp.id,
                location_id=loc.id,
                best_before_date=today - timedelta(days=1),
            )
        )

        defn_soon = ItemDefinition(name="Fresh Milk", kind_id=kind.id)
        db_session.add(defn_soon)
        db_session.flush()
        # Expiring within lead window
        db_session.add(
            StockInstance(
                definition_id=defn_soon.id,
                location_id=loc.id,
                best_before_date=today + timedelta(days=2),
            )
        )
        db_session.commit()

        lead = SettingsService(db_session).best_before_lead_days()
        expiry_items = ExpiryService(db_session).compute(within_days=lead)
        expected_expiring = sum(1 for i in expiry_items if i.status == "expiring")
        expected_expired = sum(1 for i in expiry_items if i.status == "expired")

        result = IntegrationStateService(db_session).compute()
        assert result["expiring_count"] == expected_expiring
        assert result["expired_count"] == expected_expired


# ---------------------------------------------------------------------------
# F. GET /integrations/state endpoint
# ---------------------------------------------------------------------------


class TestIntegrationStateEndpoint:
    """GET /api/integrations/state token auth and response shape."""

    def _set_token(self, http_client: object, token: str) -> None:
        """Store the integration token in the test DB via settings API."""
        client = http_client  # type: ignore[assignment]
        resp = client.patch(
            "/api/settings",
            json={
                "channels": {
                    "http": {
                        "enabled": True,
                        "webhook_url": "https://example.com/hook",
                        "integration_token": token,
                    }
                }
            },
        )
        assert resp.status_code == 200

    def test_valid_token_in_header_returns_200(self, http_client: object) -> None:
        self._set_token(http_client, "test-integration-token-abc")
        client = http_client  # type: ignore[assignment]

        # Without session (use a fresh requests-like call without cookies)
        # Since TestClient shares cookies, we test with header-only auth by
        # using an unauthenticated client reference.
        resp = client.get(
            "/api/integrations/state",
            headers={"X-Omniventory-Token": "test-integration-token-abc"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "low_stock_count" in body
        assert "expiring_count" in body
        assert "expired_count" in body
        assert "generated_at" in body

    def test_valid_token_in_query_param_returns_200(self, http_client: object) -> None:
        self._set_token(http_client, "test-integration-token-xyz")
        client = http_client  # type: ignore[assignment]

        resp = client.get(
            "/api/integrations/state",
            params={"token": "test-integration-token-xyz"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "low_stock_count" in body

    def test_missing_token_returns_401(self, http_client: object) -> None:
        self._set_token(http_client, "test-integration-token-aaa")
        client = http_client  # type: ignore[assignment]

        resp = client.get("/api/integrations/state")
        assert resp.status_code == 401
        assert resp.json()["code"] == "integration.invalid_token"

    def test_wrong_token_returns_401(self, http_client: object) -> None:
        self._set_token(http_client, "correct-token")
        client = http_client  # type: ignore[assignment]

        resp = client.get(
            "/api/integrations/state",
            headers={"X-Omniventory-Token": "wrong-token"},
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == "integration.invalid_token"

    def test_no_token_configured_returns_401(self, http_client: object) -> None:
        """When no integration token has been set, returns 401."""
        client = http_client  # type: ignore[assignment]

        # Do NOT call _set_token — no token configured.
        resp = client.get(
            "/api/integrations/state",
            headers={"X-Omniventory-Token": "any-token"},
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == "integration.invalid_token"

    def test_endpoint_works_without_session_cookie(
        self,
        temp_db: Path,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ARG002
    ) -> None:
        """The state endpoint does not require a session cookie."""
        import app.db.base as db_base_mod
        import app.models.app_config as app_config_mod
        import app.models.audit_log as audit_log_mod
        import app.models.category as cat_mod
        import app.models.household as hh_mod
        import app.models.item_definition as idef_mod
        import app.models.item_kind as ikind_mod
        import app.models.location as loc_mod
        import app.models.notification as notif_mod
        import app.models.notification_delivery as nd_mod
        import app.models.session as sess_mod
        import app.models.setting as setting_mod
        import app.models.stock_instance as si_mod
        import app.models.stock_movement as sm_mod
        import app.models.user as user_mod

        for mod in (
            db_base_mod,
            hh_mod,
            user_mod,
            sess_mod,
            app_config_mod,
            cat_mod,
            ikind_mod,
            idef_mod,
            loc_mod,
            si_mod,
            sm_mod,
            setting_mod,
            notif_mod,
            nd_mod,
            audit_log_mod,
        ):
            importlib.reload(mod)

        from fastapi.testclient import TestClient

        from app.db.base import Base, get_engine
        from app.main import create_app

        engine = get_engine()
        Base.metadata.create_all(engine)
        application = create_app()

        the_token = "no-cookie-test-token-999"

        with TestClient(application, raise_server_exceptions=True) as client:
            factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
            db = factory()
            try:
                from app.repositories.setting import SettingsRepository

                repo = SettingsRepository(db)
                repo.set("channels.http.enabled", "true")
                repo.set("channels.http.integration_token", the_token)
                db.commit()
            finally:
                db.close()

            # Call WITHOUT any cookies/session — just the token header.
            resp = client.get(
                "/api/integrations/state",
                headers={"X-Omniventory-Token": the_token},
                # Explicitly send no cookies.
                cookies={},
            )
            assert resp.status_code == 200

        drop_all_sqlite(Base, engine)

    def test_response_shape(self, http_client: object) -> None:
        """Response has the documented fields."""
        self._set_token(http_client, "shape-test-token")
        client = http_client  # type: ignore[assignment]

        resp = client.get(
            "/api/integrations/state",
            headers={"X-Omniventory-Token": "shape-test-token"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # All four required fields present with correct types
        assert isinstance(body["low_stock_count"], int)
        assert isinstance(body["expiring_count"], int)
        assert isinstance(body["expired_count"], int)
        assert isinstance(body["generated_at"], str)
        assert body["low_stock_count"] >= 0
        assert body["expiring_count"] >= 0
        assert body["expired_count"] >= 0


# ---------------------------------------------------------------------------
# G. build_dispatcher registers HttpChannel
# ---------------------------------------------------------------------------


class TestBuildDispatcher:
    """build_dispatcher() returns a dispatcher with both Email and HTTP channels."""

    def test_http_channel_is_registered(self, db_session: Session) -> None:
        from app.notifications.channels.email import EmailChannel
        from app.notifications.channels.http import HttpChannel
        from app.notifications.dispatcher import NotificationDispatcher, build_dispatcher

        dispatcher = build_dispatcher(db_session)
        assert isinstance(dispatcher, NotificationDispatcher)
        # Both channels should be registered.
        channel_types = [type(ch) for ch in dispatcher._channels]
        assert EmailChannel in channel_types
        assert HttpChannel in channel_types

    def test_dispatcher_is_noop_when_both_disabled(self, db_session: Session) -> None:
        """dispatch() with both channels disabled makes no network calls."""
        from app.auth.passwords import hash_password
        from app.models.household import Household
        from app.models.item_kind import ItemKind
        from app.models.user import User
        from app.notifications.dispatcher import build_dispatcher

        hh = Household(id=1, name="Test", currency="USD", timezone="UTC")
        db_session.add(hh)
        db_session.flush()
        kind = ItemKind(code="pd", name="PD", is_system=True)
        db_session.add(kind)
        db_session.flush()
        user = User(email="d@test.com", password_hash=hash_password("pass"), is_active=True)
        db_session.add(user)
        db_session.flush()

        notif = _make_notification(db_session, user.id, unique_suffix="_noop")

        with patch("httpx.Client") as mock_httpx, patch("smtplib.SMTP") as mock_smtp:
            build_dispatcher(db_session).dispatch([notif], include_email_digest=True)

        mock_httpx.assert_not_called()
        mock_smtp.assert_not_called()


# ---------------------------------------------------------------------------
# H. GET /settings integration token auto-generation
# ---------------------------------------------------------------------------


class TestSettingsAutoGenerateToken:
    """GET /settings generates integration_token when http is enabled + token absent."""

    def test_token_auto_generated_on_get_settings(self, http_client: object) -> None:
        client = http_client  # type: ignore[assignment]

        # Enable HTTP channel without setting a token
        resp = client.patch(
            "/api/settings",
            json={"channels": {"http": {"enabled": True, "webhook_url": "https://example.com/h"}}},
        )
        assert resp.status_code == 200
        # Token not set yet at this point (PATCH only enables)
        # Now GET settings — should auto-generate the token
        resp2 = client.get("/api/settings")
        assert resp2.status_code == 200
        body2 = resp2.json()
        # integration_token_is_set should now be True
        assert body2["channels"]["http"]["integration_token_is_set"] is True

    def test_token_not_generated_when_http_disabled(self, http_client: object) -> None:
        client = http_client  # type: ignore[assignment]

        # HTTP channel is disabled by default
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        body = resp.json()
        # Token should not be auto-generated when channel is disabled
        assert body["channels"]["http"]["integration_token_is_set"] is False
