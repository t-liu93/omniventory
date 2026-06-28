"""Authentication endpoints.

POST {prefix}/auth/login    Verify credentials → create session → set cookie.
POST {prefix}/auth/logout   Revoke server-side session + clear cookie.
GET  {prefix}/auth/me       Return current user (401 if no/invalid session).

Cookie policy (``HttpOnly`` + ``SameSite=Lax`` always; ``Secure`` in production)
----------------------------------------------------------------------------------
The ``Secure`` flag prevents the browser from sending the cookie over plain HTTP.
This is correct and required in production (HTTPS only).  However, it breaks
two scenarios in development/testing:

  1. ``TestClient`` drives requests over ``http://testserver`` — not HTTPS.
  2. ``localhost`` development without TLS.

Resolution: the ``Secure`` flag is driven by ``settings.environment``.
- ``"production"``                     → ``Secure=True``   (HTTPS required).
- ``"development"`` / ``"test"`` / *   → ``Secure=False``  (plain HTTP OK).

``HttpOnly`` and ``SameSite=Lax`` are **always** set regardless of environment,
so XSS cannot steal the token and CSRF is mitigated in all environments.

Tests that exercise ``/auth/me`` (the authenticated route) use the non-
production environment (``ENVIRONMENT=test``) so the cookie is sent back by
the TestClient's HTTP transport without needing TLS.  The production
``Secure`` requirement is verified by a separate unit test that checks the
flag logic directly (without needing HTTPS infrastructure).
"""

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.auth import sessions as session_auth
from app.auth.passwords import dummy_verify, hash_password, verify_password
from app.config import get_settings
from app.core.errors import AppError, ErrorCode, ErrorResponse
from app.core.languages import SUPPORTED_LANGUAGES
from app.db.session import get_db
from app.models.app_config import AppConfig
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    MessageResponse,
    SetupRequest,
    SetupStatusResponse,
    UserPreferencesUpdate,
    UserResponse,
)
from app.schemas.invitation import PasswordChange

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}

router = APIRouter(prefix="/auth", tags=["auth"], responses=_ERROR_RESPONSES)


def _set_session_cookie(response: Response, session_id: str) -> None:
    """Set the session cookie on ``response`` with the correct flags.

    ``HttpOnly``    Always set — JS cannot read the cookie value.
    ``SameSite``    Always ``Lax`` — safe default that allows top-level nav
                    but blocks cross-site sub-resource requests.
    ``Secure``      Set only in ``production`` — prevents sending over HTTP.
                    In dev/test this is relaxed so plain-HTTP flows work.
    """
    settings = get_settings()
    is_production = settings.environment == "production"
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=is_production,
        # No ``max_age`` — the session expiry is enforced server-side.
        # The browser will treat it as a session cookie (cleared on close),
        # but the server-side expiry is the authoritative gate.
    )


def _clear_session_cookie(response: Response) -> None:
    """Clear the session cookie from the browser."""
    settings = get_settings()
    is_production = settings.environment == "production"
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        samesite="lax",
        secure=is_production,
    )


@router.post("/login", response_model=UserResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> UserResponse:
    """Authenticate with email + password and return a session cookie.

    On success: creates a server-side session row and sets the ``HttpOnly``
    session cookie.  Returns the public user object.  Emits
    ``auth.login_succeeded`` to the audit log.

    On failure: returns 401 (email not found or wrong password).  The same
    error is returned for both cases to prevent user-enumeration attacks.
    Emits ``auth.login_failed`` (actor_user_id=NULL, actor_email=attempted
    email) and COMMITS that row BEFORE raising the 401 so it survives the
    ``get_db`` rollback triggered by the AppError exception.
    """
    from app.services.audit import AuditService

    repo = UserRepository(db)
    audit = AuditService(db)
    ip = request.client.host if request.client else None
    attempted_email = body.email.lower()

    user = repo.get_by_email(body.email)

    if user is None:
        # Consume time comparable to a real hash verification to prevent
        # user-enumeration via response timing.
        dummy_verify(body.password)
        # Record the failed attempt, then commit BEFORE raising so the row
        # survives the get_db rollback that fires when an exception propagates.
        audit.record(
            "auth.login_failed",
            actor_user_id=None,
            actor_email=attempted_email,
            ip_address=ip,
        )
        db.commit()
        raise AppError(
            ErrorCode.INVALID_CREDENTIALS,
            status_code=401,
            message="Invalid credentials",
        )

    if not verify_password(body.password, user.password_hash):
        audit.record(
            "auth.login_failed",
            actor_user_id=None,
            actor_email=attempted_email,
            ip_address=ip,
        )
        db.commit()
        raise AppError(
            ErrorCode.INVALID_CREDENTIALS,
            status_code=401,
            message="Invalid credentials",
        )

    if not user.is_active:
        raise AppError(
            ErrorCode.ACCOUNT_DISABLED,
            status_code=401,
            message="Account is disabled",
        )

    session = session_auth.create(db, user.id)
    _set_session_cookie(response, session.id)

    # Audit the successful login (committed by get_db on success).
    audit.record(
        "auth.login_succeeded",
        actor_user_id=user.id,
        actor_email=user.email,
        target_type="user",
        target_id=user.id,
        ip_address=ip,
    )

    return UserResponse.model_validate(user)


@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Revoke the current session and clear the cookie.

    Idempotent: if the cookie is absent or the session is already gone, the
    endpoint still returns 200 and clears the cookie.

    Emits ``auth.logout`` (best-effort: if the cookie's session resolves to a
    user we log with that actor; otherwise we skip — don't break idempotent
    logout).
    """
    from app.models.session import Session as SessionModel
    from app.services.audit import AuditService

    settings = get_settings()
    session_id: str | None = request.cookies.get(settings.session_cookie_name)

    # Resolve the actor BEFORE revoking (the session row is deleted by revoke).
    actor = None
    if session_id:
        sess = db.get(SessionModel, session_id)
        if sess is not None:
            actor = UserRepository(db).get_by_id(sess.user_id)
        session_auth.revoke(db, session_id)

    _clear_session_cookie(response)

    # Best-effort: audit only when we could resolve the user from the session.
    if actor is not None:
        ip = request.client.host if request.client else None
        AuditService(db).record(
            "auth.logout",
            actor_user_id=actor.id,
            actor_email=actor.email,
            ip_address=ip,
        )

    return MessageResponse(message="Logged out successfully")


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user)) -> MeResponse:
    """Return the currently authenticated user.

    Requires a valid session cookie.  Returns 401 if absent / expired.
    """
    return MeResponse(user=UserResponse.model_validate(user))


@router.patch("/me", response_model=MeResponse)
def update_me(
    body: UserPreferencesUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    """Update per-user preferences for the currently authenticated user.

    PATCH semantics — field omission vs explicit null
    -------------------------------------------------
    - Field **omitted** from the JSON body: no-op; the stored value is left
      unchanged.
    - Field set to **null** explicitly: writes NULL to the DB, re-enabling the
      client-side resolution chain (localStorage → navigator → 'en').
    - Field set to a **string**: validated against ``SUPPORTED_LANGUAGES``; on
      success the value is persisted.

    Validation
    ----------
    An unsupported language code raises ``AppError(validation.unsupported_language, 422)``
    and no write happens.

    Returns the updated ``MeResponse``.
    """
    repo = UserRepository(db)
    needs_commit = False

    if "preferred_language" in body.model_fields_set:
        lang = body.preferred_language
        if lang is not None and lang not in SUPPORTED_LANGUAGES:
            raise AppError(
                ErrorCode.UNSUPPORTED_LANGUAGE,
                status_code=422,
                params={"value": lang, "supported": list(SUPPORTED_LANGUAGES)},
            )
        repo.set_preferred_language(user, lang)
        needs_commit = True

    if "reminder_best_before_lead_days" in body.model_fields_set:
        # Pydantic ge=0 is the sole validation guard; the value is already
        # validated by the schema before reaching this point.
        repo.set_reminder_best_before_lead_days(user, body.reminder_best_before_lead_days)
        needs_commit = True

    if "reminder_warranty_lead_days" in body.model_fields_set:
        repo.set_reminder_warranty_lead_days(user, body.reminder_warranty_lead_days)
        needs_commit = True

    # M6 Step 5: per-user channel opt-outs.  The columns are NOT NULL, so an
    # explicit null is treated as a no-op (only write when the value is a bool).
    if "notify_in_app" in body.model_fields_set and body.notify_in_app is not None:
        repo.set_notify_prefs(user, notify_in_app=body.notify_in_app)
        needs_commit = True

    if "notify_email_digest" in body.model_fields_set and body.notify_email_digest is not None:
        repo.set_notify_prefs(user, notify_email_digest=body.notify_email_digest)
        needs_commit = True

    if needs_commit:
        db.commit()
        db.refresh(user)

    return MeResponse(user=UserResponse.model_validate(user))


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    body: PasswordChange,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Change the current user's password (any authenticated user).

    Verifies ``current_password`` against the stored hash.  On mismatch
    returns 400 ``auth.password_incorrect``.  On success, sets the new
    password hash and revokes all OTHER sessions for this user (the current
    session remains active, so the caller stays logged in).

    Emits ``password.changed`` on success (committed by ``get_db``).

    Error codes:
    - 400 ``auth.password_incorrect`` — wrong current password.
    """
    from app.services.audit import AuditService
    from app.services.invitation import InvitationService

    settings = get_settings()
    session_id: str | None = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        # Should not happen — get_current_user already verified the cookie.
        raise AppError(ErrorCode.NOT_AUTHENTICATED, status_code=401)

    svc = InvitationService(db)
    svc.change_password(user, body.current_password, body.new_password, session_id)

    ip = request.client.host if request.client else None
    AuditService(db).record(
        "password.changed",
        actor_user_id=user.id,
        actor_email=user.email,
        target_type="user",
        target_id=user.id,
        ip_address=ip,
    )

    return MessageResponse(message="Password changed successfully.")


# ---------------------------------------------------------------------------
# First-run onboarding endpoints (unauthenticated)
# ---------------------------------------------------------------------------


@router.get("/setup-status", response_model=SetupStatusResponse)
def setup_status(db: Session = Depends(get_db)) -> SetupStatusResponse:
    """Return whether first-run setup is still required.

    ``setup_required: true``  — no users exist; the setup page must be shown.
    ``setup_required: false`` — at least one user exists; show the login page.

    Unauthenticated — the frontend calls this on every load to decide which
    page to show before the user has any session cookie.
    """
    repo = UserRepository(db)
    return SetupStatusResponse(setup_required=repo.count() == 0)


_ONBOARDING_SENTINEL_KEY = "onboarding_completed"


@router.post("/setup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def setup(
    body: SetupRequest,
    db: Session = Depends(get_db),
) -> UserResponse:
    """Create the first admin user (first-run onboarding).

    Self-closing and concurrency-safe: the admin user and a sentinel row
    (``app_config.key = 'onboarding_completed'``) are created in a **single
    transaction**.  Because ``app_config.key`` is the primary key, a second
    concurrent request that also passes the fast-path pre-check will hit a
    primary-key ``IntegrityError`` when it tries to insert the same sentinel,
    causing its transaction to roll back → 409.

    This works even when two concurrent requests each read ``count == 0`` with
    *different* emails (which would bypass the email-unique constraint alone):
    only one can win the sentinel insert; the loser always gets 409.

    Fast-path pre-check (sentinel exists or any user exists → 409) is kept for
    the common case, but the correctness guarantee comes from the unique-key
    sentinel insert, not from the pre-check.

    On success returns the created user (HTTP 201).  Does NOT auto-login —
    the frontend transitions to the normal login screen after setup.
    """
    repo = UserRepository(db)

    # Fast-path: sentinel already written or user already exists → skip the
    # expensive password hash and return immediately.
    sentinel_exists = db.get(AppConfig, _ONBOARDING_SENTINEL_KEY) is not None
    if sentinel_exists or repo.count() > 0:
        raise AppError(
            ErrorCode.SETUP_ALREADY_COMPLETE,
            status_code=409,
            message="Setup already complete: an admin user already exists.",
        )

    # Insert both the user and the sentinel atomically.  If another concurrent
    # request races to the same point, one of them will raise IntegrityError on
    # the sentinel's primary-key uniqueness → translated to 409 below.
    user = repo.create(
        email=body.email,
        password_hash=hash_password(body.password),
        role="admin",
        is_active=True,
    )
    db.flush()  # Assign user.id before inserting the sentinel.
    sentinel = AppConfig(key=_ONBOARDING_SENTINEL_KEY, value="true")
    db.add(sentinel)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(
            ErrorCode.SETUP_ALREADY_COMPLETE,
            status_code=409,
            message="Setup already complete: an admin user already exists.",
        ) from None

    db.refresh(user)
    return UserResponse.model_validate(user)
