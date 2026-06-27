"""Invitation, password-reset, and self change-password service (M6 Step 3).

Business logic for the token-based flows (§4.3):

``InvitationService``
    ``create_invite(email, role, *, created_by, request)``
        Validate, revoke any prior pending invite for that email, mint a new
        token, optionally send an email, return ``(token, raw_token, emailed)``.
    ``list_pending()``
        List all current pending invites.
    ``revoke(invite_id)``
        Delete a pending invite by id; 404 if not found.
    ``validate_invite(raw_token)``
        Hash, look up, check purpose/consumed/expired; 400 ``auth.invalid_token`` on failure.
    ``accept_invite(raw_token, password)``
        Validate → create user → consume → return ``User``.  Does NOT auto-login.
    ``issue_reset(user_id, *, created_by, request)``
        Revoke prior resets, mint a ``password_reset`` token, return ``(reset_url, emailed)``.
    ``validate_reset(raw_token)``
        Like ``validate_invite`` but for ``purpose="password_reset"``.
    ``accept_reset(raw_token, password)``
        Validate → set password → consume → revoke all user sessions → return ``User``.
    ``change_password(user, current_password, new_password, current_session_id)``
        Verify current → 400 ``auth.password_incorrect``; set new hash → revoke
        OTHER sessions (keep ``current_session_id``).

Design notes
------------
- Raw tokens are NEVER stored; only the sha256 hex hash is persisted.
- Email sends are **best-effort**: SMTP errors are caught + logged; the request
  succeeds regardless.  The ``emailed`` bool reflects success.
- Race guard in ``accept_invite``: if the email was registered as a user between
  invite creation and accept, we return ``auth.invalid_token`` (not
  ``user.email_exists``) to avoid leaking info to the anonymous caller.
- The ``accept_reset`` flow revokes ALL sessions for the user (including the one
  that might have been used to request the reset) since the admin triggered it —
  the user should re-authenticate after changing their password.
- ``change_password`` keeps the current session alive (``except_session_id``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password, verify_password
from app.auth.permissions import VALID_ROLES
from app.auth.sessions import revoke_all_for_user
from app.auth.tokens import hash_token, invite_expires_at, mint_token, reset_expires_at
from app.core.errors import AppError, ErrorCode
from app.models.user import User
from app.models.user_token import UserToken
from app.notifications.channels.email import EmailChannel
from app.repositories.user import UserRepository
from app.repositories.user_token import UserTokenRepository

logger = logging.getLogger(__name__)


def _as_utc(dt: datetime) -> datetime:
    """Attach UTC tzinfo if the datetime is offset-naive (SQLite round-trip)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _mask_email(email: str) -> str:
    """Return a masked version of *email* suitable for the password-reset public endpoint.

    Example: ``user@example.com`` → ``u**r@e*****e.com``

    This is a hint (not a secret) to confirm which account the reset link was
    sent to, without exposing the full address to an anonymous caller.
    """
    at = email.rfind("@")
    if at <= 0:
        return "***"
    local = email[:at]
    domain = email[at + 1 :]

    # Mask local: keep first char + last char (if length > 2), fill with '*'.
    if len(local) <= 1:
        local_masked = local
    elif len(local) == 2:
        local_masked = local[0] + "*"
    else:
        local_masked = local[0] + "*" * (len(local) - 2) + local[-1]

    # Mask domain: keep first char + everything from the last dot.
    dot = domain.rfind(".")
    if dot > 0:
        domain_name = domain[:dot]
        tld = domain[dot:]
        if len(domain_name) <= 1:
            domain_masked = domain_name
        else:
            domain_masked = domain_name[0] + "*" * (len(domain_name) - 1)
        domain_masked += tld
    else:
        domain_masked = "*" * len(domain)

    return f"{local_masked}@{domain_masked}"


class InvitationService:
    """Business-logic facade for invitations, password resets, and change-password."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._user_repo = UserRepository(db)
        self._token_repo = UserTokenRepository(db)

    # ---------------------------------------------------------------------- #
    # Invitations                                                              #
    # ---------------------------------------------------------------------- #

    def create_invite(
        self,
        email: str,
        role: str,
        *,
        created_by: int,
        request: Request,
    ) -> tuple[UserToken, str, bool]:
        """Create a new invitation for *email* with *role*.

        Flow:
        1. Lower-case the email.
        2. Reject if ``role`` is not a valid role string (422).
        3. Reject if a user with that email already exists (409 ``user.email_exists``).
        4. Revoke (delete) any prior pending invite for that email.
        5. Mint token; insert row.
        6. Best-effort SMTP send.
        7. Return ``(token_row, raw_token, emailed)``.

        The ``accept_url`` is NOT stored here; it is built in the route from
        ``raw_token`` and the request base URL (§4.3: "the raw token is returned
        to the admin exactly once").

        Parameters
        ----------
        email:
            The invitee's email address (lower-cased internally).
        role:
            The role the invitee will receive on accept (``admin``/``member``/``viewer``).
        created_by:
            The id of the admin who issued the invite.
        request:
            The FastAPI ``Request`` object, used to derive ``app_origin`` for the link.

        Returns
        -------
        (token_row, raw_token, emailed)
            ``token_row``  — the persisted ``UserToken`` ORM object.
            ``raw_token``  — the URL-safe random token (embedded in the accept URL).
            ``emailed``    — ``True`` when SMTP was configured and the email was sent.

        Raises
        ------
        AppError(INVALID_INPUT, 422)
            When *role* is not a valid role string.
        AppError(USER_EMAIL_EXISTS, 409)
            When a user with *email* already exists.
        """
        email = email.lower()

        # Validate role against the allowed set.
        if role not in VALID_ROLES:
            valid = ", ".join(sorted(VALID_ROLES))
            raise AppError(
                ErrorCode.INVALID_INPUT,
                status_code=422,
                params={"value": role, "supported": list(VALID_ROLES)},
                message=f"role must be one of: {valid}",
            )

        # Reject if the email is already registered as a user.
        if self._user_repo.get_by_email(email) is not None:
            raise AppError(ErrorCode.USER_EMAIL_EXISTS, status_code=409)

        # Revoke any prior pending invite for this email.
        prior = self._token_repo.get_pending_invite_by_email(email)
        if prior is not None:
            self._token_repo.delete(prior)

        # Mint token and persist.
        raw_token, token_hash = mint_token()
        expires_at = invite_expires_at()
        token = self._token_repo.create(
            purpose="invite",
            email=email,
            role=role,
            token_hash=token_hash,
            expires_at=expires_at,
            created_by=created_by,
        )

        # Build the accept URL (zero-config: base_url from the request).
        # For a single-container deployment, ``request.base_url`` reflects
        # the scheme + host + port that the client used to reach the app.
        app_origin = str(request.base_url).rstrip("/")
        accept_url = f"{app_origin}/invite/accept?token={raw_token}"

        # Best-effort SMTP send: errors are caught + logged; ``emailed`` is False on failure.
        emailed = self._try_send_email(
            to=email,
            subject="You're invited to Omniventory",
            body=(
                f'You have been invited to join Omniventory with the role "{role}".\n\n'
                f"Accept your invitation here:\n\n{accept_url}\n\n"
                f"This link expires in 7 days."
            ),
        )

        return token, raw_token, emailed

    def list_pending(self) -> list[UserToken]:
        """Return all currently pending invites (not consumed, not expired)."""
        return self._token_repo.list_pending_invites()

    def revoke(self, invite_id: int) -> None:
        """Delete a pending invite by id.

        Raises
        ------
        AppError(INVITATION_NOT_FOUND, 404)
            When no token row with *invite_id* exists.
        """
        token = self._token_repo.get_by_id(invite_id)
        if token is None or token.purpose != "invite":
            raise AppError(ErrorCode.INVITATION_NOT_FOUND, status_code=404)
        self._token_repo.delete(token)

    def validate_invite(self, raw_token: str) -> UserToken:
        """Validate an invite token and return the row.

        Checks:
        - Token exists in the DB.
        - ``purpose == "invite"``.
        - ``consumed_at IS NULL``.
        - ``expires_at`` is still in the future.

        Raises
        ------
        AppError(AUTH_INVALID_TOKEN, 400)
            On any validation failure.
        """
        token = self._token_repo.get_by_token_hash(hash_token(raw_token))
        if token is None or token.purpose != "invite":
            raise AppError(ErrorCode.AUTH_INVALID_TOKEN, status_code=400)
        if token.consumed_at is not None:
            raise AppError(ErrorCode.AUTH_INVALID_TOKEN, status_code=400)
        if datetime.now(UTC) >= _as_utc(token.expires_at):
            raise AppError(ErrorCode.AUTH_INVALID_TOKEN, status_code=400)
        return token

    def accept_invite(self, raw_token: str, password: str) -> User:
        """Validate the invite token and create the user.

        Flow:
        1. ``validate_invite`` (purpose, consumed, expired).
        2. Guard race: if the email became a user in the meantime → 400 ``auth.invalid_token``.
        3. Create the user with (email, role) from the token, hash the password.
        4. Consume the token (set ``consumed_at``).
        5. Return the new ``User``.  Does NOT auto-login.

        Raises
        ------
        AppError(AUTH_INVALID_TOKEN, 400)
            On invalid / expired / consumed token or duplicate-email race.
        """
        token = self.validate_invite(raw_token)

        # An invite token always has email and role set (non-null by design).
        # If either is somehow missing the token is corrupt — treat as invalid.
        if not token.email or not token.role:
            raise AppError(ErrorCode.AUTH_INVALID_TOKEN, status_code=400)

        # Race guard: the email may have been registered between invite creation
        # and accept (e.g. by a concurrent accept or a direct admin create).
        if self._user_repo.get_by_email(token.email) is not None:
            raise AppError(ErrorCode.AUTH_INVALID_TOKEN, status_code=400)

        try:
            user = self._user_repo.create(
                email=token.email,  # already lower-cased at invite time; narrowed above
                password_hash=hash_password(password),
                role=token.role,  # narrowed above
                is_active=True,
            )
        except IntegrityError:
            # True-concurrent accept: a second request slipped through the
            # get_by_email guard and hit the users.email unique constraint.
            # Roll back so the session is clean, then surface a 400 — same
            # semantics as the explicit race guard above.
            self._db.rollback()
            raise AppError(ErrorCode.AUTH_INVALID_TOKEN, status_code=400) from None
        self._token_repo.mark_consumed(token, datetime.now(UTC))
        return user

    # ---------------------------------------------------------------------- #
    # Password reset                                                           #
    # ---------------------------------------------------------------------- #

    def issue_reset(
        self,
        user_id: int,
        *,
        created_by: int,
        request: Request,
    ) -> tuple[str, bool]:
        """Issue a password-reset link for *user_id*.

        Flow:
        1. 404 ``user.not_found`` if the user doesn't exist.
        2. Revoke any prior pending resets for that user.
        3. Mint a ``password_reset`` token with ``RESET_TTL_HOURS`` expiry.
        4. Best-effort SMTP send to the user's email.
        5. Return ``(reset_url, emailed)``.

        Parameters
        ----------
        user_id:
            The id of the user whose password is being reset.
        created_by:
            The admin user id who initiated the reset.
        request:
            The FastAPI ``Request`` object (for app_origin derivation).

        Returns
        -------
        (reset_url, emailed)

        Raises
        ------
        AppError(USER_NOT_FOUND, 404)
            When no user with *user_id* exists.
        """
        user = self._user_repo.get_by_id(user_id)
        if user is None:
            raise AppError(ErrorCode.USER_NOT_FOUND, status_code=404)

        # Revoke any prior pending resets for this user.
        for prior in self._token_repo.list_pending_resets_for_user(user_id):
            self._token_repo.delete(prior)

        # Mint and persist the reset token.
        raw_token, token_hash = mint_token()
        expires_at = reset_expires_at()
        self._token_repo.create(
            purpose="password_reset",
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_by=created_by,
        )

        # Build the reset URL.
        app_origin = str(request.base_url).rstrip("/")
        reset_url = f"{app_origin}/password-reset/accept?token={raw_token}"

        # Best-effort SMTP send.
        emailed = self._try_send_email(
            to=user.email,
            subject="Password reset for Omniventory",
            body=(
                "A password reset was requested for your Omniventory account.\n\n"
                f"Reset your password here:\n\n{reset_url}\n\n"
                "This link expires in 24 hours.\n\n"
                "If you did not request this, you can ignore this email."
            ),
        )

        return reset_url, emailed

    def validate_reset(self, raw_token: str) -> UserToken:
        """Validate a password-reset token and return the row.

        Same checks as ``validate_invite`` but requires ``purpose="password_reset"``.

        Raises
        ------
        AppError(AUTH_INVALID_TOKEN, 400)
            On any validation failure.
        """
        token = self._token_repo.get_by_token_hash(hash_token(raw_token))
        if token is None or token.purpose != "password_reset":
            raise AppError(ErrorCode.AUTH_INVALID_TOKEN, status_code=400)
        if token.consumed_at is not None:
            raise AppError(ErrorCode.AUTH_INVALID_TOKEN, status_code=400)
        if datetime.now(UTC) >= _as_utc(token.expires_at):
            raise AppError(ErrorCode.AUTH_INVALID_TOKEN, status_code=400)
        return token

    def accept_reset(self, raw_token: str, password: str) -> User:
        """Accept a password-reset token and update the user's password.

        Flow:
        1. ``validate_reset`` (purpose, consumed, expired).
        2. Fetch the target user (400 on missing — should not happen, but safe).
        3. Set the new ``password_hash``.
        4. Consume the token.
        5. Revoke ALL sessions for this user (they must re-authenticate).
        6. Return the updated ``User``.

        Raises
        ------
        AppError(AUTH_INVALID_TOKEN, 400)
            On invalid / expired / consumed token or missing user.
        """
        token = self.validate_reset(raw_token)

        # A password_reset token always has user_id set (non-null by design).
        if token.user_id is None:
            raise AppError(ErrorCode.AUTH_INVALID_TOKEN, status_code=400)

        user = self._user_repo.get_by_id(token.user_id)
        if user is None:
            # The user was deleted after the reset was issued — treat as invalid.
            raise AppError(ErrorCode.AUTH_INVALID_TOKEN, status_code=400)

        self._user_repo.set_password_hash(user, hash_password(password))
        self._token_repo.mark_consumed(token, datetime.now(UTC))

        # Revoke all sessions so the user must log in with the new password.
        revoke_all_for_user(self._db, user.id)

        return user

    def get_reset_email_masked(self, raw_token: str) -> str:
        """Validate a reset token and return the masked email for the public form.

        Called by ``GET /password-reset/accept?token=`` to render the form.
        Returns ``email_masked`` so the user sees a hint about which account
        the reset is for, without exposing the full address to an anonymous caller.

        Raises
        ------
        AppError(AUTH_INVALID_TOKEN, 400)
            On any validation failure.
        """
        token = self.validate_reset(raw_token)
        if token.user_id is None:
            raise AppError(ErrorCode.AUTH_INVALID_TOKEN, status_code=400)
        user = self._user_repo.get_by_id(token.user_id)
        if user is None:
            raise AppError(ErrorCode.AUTH_INVALID_TOKEN, status_code=400)
        return _mask_email(user.email)

    # ---------------------------------------------------------------------- #
    # Self change-password                                                     #
    # ---------------------------------------------------------------------- #

    def change_password(
        self,
        user: User,
        current_password: str,
        new_password: str,
        current_session_id: str,
    ) -> None:
        """Verify the current password, then set a new one.

        Flow:
        1. Verify ``current_password`` against ``user.password_hash``.
        2. On mismatch → 400 ``auth.password_incorrect``.
        3. On match → set the new ``password_hash``.
        4. Revoke all OTHER sessions for this user (keep ``current_session_id``
           so the caller remains logged in).

        Parameters
        ----------
        user:
            The currently authenticated user (from ``get_current_user``).
        current_password:
            The plaintext current password to verify.
        new_password:
            The plaintext new password to hash and store.
        current_session_id:
            The active session id to keep alive after revocation.

        Raises
        ------
        AppError(AUTH_PASSWORD_INCORRECT, 400)
            When ``current_password`` does not match the stored hash.
        """
        if not verify_password(current_password, user.password_hash):
            raise AppError(ErrorCode.AUTH_PASSWORD_INCORRECT, status_code=400)

        self._user_repo.set_password_hash(user, hash_password(new_password))

        # Revoke all OTHER sessions (keep the current one active).
        revoke_all_for_user(self._db, user.id, except_session_id=current_session_id)

    # ---------------------------------------------------------------------- #
    # Internal helpers                                                         #
    # ---------------------------------------------------------------------- #

    def _try_send_email(self, *, to: str, subject: str, body: str) -> bool:
        """Best-effort SMTP send.  Returns True on success, False on any failure.

        Checks ``EmailChannel.is_enabled()`` first; if disabled, returns False
        immediately (no error).  Any SMTP exception is caught, logged, and
        swallowed — the invitation/reset flow must succeed regardless.
        """
        channel = EmailChannel(self._db)
        if not channel.is_enabled():
            return False
        try:
            channel.send_transactional(to, subject, body)
            return True
        except Exception:
            logger.exception(
                "InvitationService: failed to send transactional email to %s (subject=%r).",
                to,
                subject,
            )
            return False
