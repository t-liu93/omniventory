"""Email digest channel adapter (M4 §4.6 / §9 Step 7).

``EmailChannel`` implements the ``NotificationChannel`` protocol for sending a
daily digest email via SMTP (stdlib ``smtplib``).

Architecture
------------
- **Digest-only**: the channel only acts when ``include_email_digest=True``.
  On the event-trigger path (``include_email_digest=False``) the channel is a
  complete no-op.  This matches M4 §2: "Email is digest-only (bundled at the
  end of the daily scan)".
- **Per-recipient grouping**: each active user with new notifications gets one
  email in their preferred language (``preferred_language or 'en'``).
- **Idempotency**: before building a digest for a notification, the channel
  checks ``NotificationDeliveryRepository.exists_sent(nid, 'email')``.
  Already-delivered notifications are skipped; the email is only sent when at
  least one eligible (not-yet-sent) notification remains.
- **Best-effort**: SMTP/rendering errors are caught, logged, and recorded as
  ``status='failed'`` delivery rows.  They are **never propagated** — a channel
  error must not crash a scan or a movement hook.
- **Post-commit I/O**: the caller (``_run_scan_job`` / ``POST /reminders/run``)
  commits the notification rows *before* calling ``dispatch()``.  Network I/O
  therefore happens after DB commit, which is correct (M4 §2: "Network I/O
  happens after the notification rows commit").

Configuration (all via ``SettingsService``)
-------------------------------------------
- ``channels.email.enabled``      — master on/off switch.
- ``channels.email.host``         — SMTP server hostname (required for enable).
- ``channels.email.port``         — SMTP port (default system default when None).
- ``channels.email.username``     — SMTP auth username (optional).
- ``channels.email.password``     — SMTP auth password (write-only secret).
- ``channels.email.encryption``   — ``"none"`` | ``"starttls"`` | ``"ssl"``.
- ``channels.email.from_address`` — envelope From address.
- ``channels.email.from_name``    — optional From display name.

The channel is considered **enabled** when ``enabled=True`` AND ``host`` is
non-empty.  Any other combination is treated as disabled (no-op).
"""

from __future__ import annotations

import json
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.notifications.messages import render_digest, render_line, render_test_email
from app.repositories.notification_delivery import NotificationDeliveryRepository
from app.repositories.user import UserRepository
from app.services.settings import SettingsService

if TYPE_CHECKING:
    from app.models.notification import Notification

logger = logging.getLogger(__name__)

# Sentinel string for the delivery channel name (M4 §3.6).
_CHANNEL_NAME = "email"


class EmailChannel:
    """SMTP email digest channel adapter (implements ``NotificationChannel``).

    Parameters
    ----------
    db:
        Active SQLAlchemy session.  Used to read settings, look up recipients,
        and record delivery rows.  Must outlive this channel instance.
    """

    def __init__(self, db: Session) -> None:
        self._db = db
        self._settings = SettingsService(db)
        self._user_repo = UserRepository(db)
        self._delivery_repo = NotificationDeliveryRepository(db)

    # ------------------------------------------------------------------
    # NotificationChannel protocol
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """Return True when email is enabled and a host is configured.

        The channel is enabled when:
        1. ``channels.email.enabled`` is True, AND
        2. ``channels.email.host`` is non-empty.

        If either condition fails the channel is disabled and ``deliver()``
        is a complete no-op.

        Uses the public ``email_channel_config()`` getter (Step 8 minor fix:
        no direct access to the private ``_get_value`` method).
        """
        cfg = self._settings.email_channel_config()
        return bool(cfg.enabled and cfg.host)

    def deliver(
        self,
        notifications: list[Notification],
        *,
        include_email_digest: bool,
    ) -> None:
        """Send a digest email per recipient for the given new notifications.

        Only acts when ``include_email_digest=True`` (daily-scan path).  On the
        event-trigger path (``include_email_digest=False``) this method is a
        complete no-op — email is digest-only (M4 §2).

        Per-recipient flow:
        1. Group the incoming notifications by ``user_id``.
        2. For each recipient, filter out notifications already sent on the
           email channel (idempotency via ``exists_sent``).
        3. If any remain, look up the recipient, build a digest in their
           language, and send via SMTP.
        4. Record one delivery row per notification covered by the digest
           (``status='sent'`` on success, ``status='failed'`` on error).

        Errors are caught, logged, and recorded as ``'failed'`` rows — never
        raised.  A failed digest for one recipient does not affect other
        recipients.

        Parameters
        ----------
        notifications:
            List of newly committed ``Notification`` rows to consider.
        include_email_digest:
            Must be ``True`` for this method to act; ``False`` → no-op.
        """
        if not include_email_digest:
            # Event-trigger path: email is digest-only, skip silently.
            return

        if not notifications:
            return

        # --- Group by user_id -------------------------------------------
        by_user: dict[int, list[Notification]] = {}
        for n in notifications:
            by_user.setdefault(n.user_id, []).append(n)

        # --- Per-recipient delivery -------------------------------------
        for user_id, user_notifs in by_user.items():
            self._deliver_to_recipient(user_id, user_notifs)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _deliver_to_recipient(
        self,
        user_id: int,
        notifications: list[Notification],
    ) -> None:
        """Build and send one digest email for a single recipient.

        Filters already-sent notifications; if nothing remains, skips silently.
        Catches all errors and records ``'failed'`` delivery rows.
        """
        # Filter to notifications not yet sent on the email channel.
        pending = [
            n for n in notifications if not self._delivery_repo.exists_sent(n.id, _CHANNEL_NAME)
        ]
        if not pending:
            return  # All already delivered — nothing to do.

        try:
            user = self._user_repo.get_by_id(user_id)
            if user is None:
                logger.warning("EmailChannel: user_id=%d not found; skipping digest.", user_id)
                return

            # M6 Step 5 — email digest opt-out: skip building/sending without
            # recording any delivery rows.  Notification rows (created by the
            # engine) may still exist to power the in-app inbox; we simply do
            # not send the email for this recipient.
            if not user.notify_email_digest:
                logger.debug(
                    "EmailChannel: user_id=%d has notify_email_digest=False; skipping digest.",
                    user_id,
                )
                return

            lang = user.preferred_language or "en"
            recipient_email = user.email

            # Render each notification as a line.
            lines: list[str] = []
            for n in pending:
                params: dict[str, object] = {}
                if n.params:
                    try:
                        params = json.loads(n.params)
                    except (ValueError, TypeError):
                        params = {}
                lines.append(render_line(n.message_code, params, lang))

            subject, body = render_digest(lines, lang)

            self._send_smtp(recipient_email, subject, body)

            # Record 'sent' for each covered notification.
            for n in pending:
                self._delivery_repo.record(
                    notification_id=n.id,
                    channel=_CHANNEL_NAME,
                    status="sent",
                )
            logger.info(
                "EmailChannel: digest sent to %s (%d notification(s)).",
                recipient_email,
                len(pending),
            )

        except Exception as exc:
            # Best-effort: record 'failed' for all pending notifications, then
            # swallow the exception.  A broken SMTP config must not crash the
            # scan or roll back the notification rows.
            detail = str(exc)
            logger.exception(
                "EmailChannel: failed to send digest to user_id=%d: %s",
                user_id,
                detail,
            )
            for n in pending:
                try:
                    self._delivery_repo.record(
                        notification_id=n.id,
                        channel=_CHANNEL_NAME,
                        status="failed",
                        detail=detail,
                    )
                except Exception:
                    # If we can't even record the failure, log and move on.
                    logger.exception(
                        "EmailChannel: could not record 'failed' delivery for notification_id=%s",
                        n.id,
                    )

    def send_transactional(self, to_address: str, subject: str, body: str) -> None:
        """Send a one-off transactional email (not a digest).

        Used by ``InvitationService`` (M6 Step 3) to deliver invite links and
        password-reset links when SMTP is configured.  Raises on any SMTP error
        — the caller must catch if best-effort delivery is required.

        Does NOT log to ``notification_deliveries`` — there is no
        ``Notification`` row for a transactional send.

        Parameters
        ----------
        to_address:
            Recipient email address.
        subject:
            Email subject line.
        body:
            Plain-text email body.
        """
        self._send_smtp(to_address, subject, body)

    def send_test(self, to_address: str, lang: str) -> None:
        """Send a test email to ``to_address`` using the currently-saved settings.

        Renders a bilingual test subject/body via ``render_test_email`` and
        delivers it via ``_send_smtp``.  Raises on any SMTP error; the caller
        (the API route) wraps it in a try/except to produce the diagnostic result.

        Does NOT log to ``notification_deliveries`` — there is no Notification
        row for a test send.
        """
        subject, body = render_test_email(lang)
        self._send_smtp(to_address, subject, body)

    def _send_smtp(self, to_address: str, subject: str, body: str) -> None:
        """Send a plain-text email via SMTP using the configured settings.

        Supports three encryption modes (``encryption`` field):
        - ``"ssl"``:      connect via ``smtplib.SMTP_SSL`` (implicit TLS).
        - ``"starttls"``: connect via ``smtplib.SMTP`` then call ``smtp.starttls()``.
        - ``"none"``:     connect via ``smtplib.SMTP``, no TLS.

        The ``from_name`` field (when set) is used with ``email.utils.formataddr``
        to produce a "Display Name <addr>" From header.

        Raises on any SMTP error — callers must catch.

        Uses the public ``email_channel_config()`` getter (Step 8 minor fix:
        no direct access to the private ``_get_value`` method).
        """
        cfg = self._settings.email_channel_config()
        host: str = cfg.host  # type: ignore[assignment]
        port: int | None = cfg.port
        username: str | None = cfg.username
        password: str | None = cfg.password
        encryption: str = cfg.encryption
        from_address: str | None = cfg.from_address
        from_name: str | None = cfg.from_name

        raw_from_addr = from_address or username or f"omniventory@{host}"
        # Build the From header with optional display name.
        from_header = formataddr((from_name, raw_from_addr)) if from_name else raw_from_addr

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_header
        msg["To"] = to_address
        msg.set_content(body)

        # Connect to SMTP (port is optional — pass 0 to let smtplib use the default).
        smtp_port: int = int(port) if port is not None else 0

        if encryption == "ssl":
            # Implicit TLS — use SMTP_SSL from the start.
            with smtplib.SMTP_SSL(host=host, port=smtp_port) as smtp:
                if username and password:
                    smtp.login(username, password)
                smtp.send_message(msg)
        else:
            # "starttls" or "none" — plain SMTP, optionally upgraded.
            with smtplib.SMTP(host=host, port=smtp_port) as smtp:
                if encryption == "starttls":
                    smtp.starttls()
                if username and password:
                    smtp.login(username, password)
                smtp.send_message(msg)
