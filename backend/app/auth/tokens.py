"""One-time token minting and hashing for invitations and password resets.

Design (M6 Step 3 §2 / §4.3)
-------------------------------
- The **raw token** is ``secrets.token_urlsafe(32)`` — 256 bits of entropy.
  It is returned to the caller **exactly once** and is **never stored** in the
  DB.  The caller embeds it in a URL and the recipient clicks the link.

- The **stored value** is ``hashlib.sha256(raw.encode()).hexdigest()`` — a
  64-character hex string.  This is what goes into ``user_tokens.token_hash``.
  A DB leak never exposes a live link: recovering the raw token from the hash
  requires a full preimage attack on SHA-256.

- ``INVITE_TTL_DAYS`` and ``RESET_TTL_HOURS`` are module-level constants so
  tests and services can reference them without hard-coding magic numbers.

Public API
----------
``mint_token()``        Mint a new (raw_token, token_hash) pair.
``hash_token(raw)``     Hash a raw token to look it up in the DB.
``invite_expires_at()`` Compute invite expiry (now + 7 days).
``reset_expires_at()``  Compute reset expiry (now + 24 hours).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Lifetime of an invitation token.
INVITE_TTL_DAYS: int = 7

#: Lifetime of a password-reset token.
RESET_TTL_HOURS: int = 24


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def mint_token() -> tuple[str, str]:
    """Mint a new one-time token.

    Returns ``(raw_token, token_hash)`` where:
    - ``raw_token``   is URL-safe random bytes encoded as base64 (43 chars).
                      **Return this to the caller exactly once — never store it.**
    - ``token_hash``  is the sha256 hex digest of the raw token (64 chars).
                      **Only this is stored in the DB.**

    Entropy: ``secrets.token_urlsafe(32)`` provides 256 bits.
    """
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return raw, token_hash


def hash_token(raw: str) -> str:
    """Hash a raw token string to its stored form (sha256 hex, 64 chars).

    Call this at accept-time to look up the row::

        token_hash = hash_token(raw_from_query_param)
        row = repo.get_by_token_hash(token_hash)
    """
    return hashlib.sha256(raw.encode()).hexdigest()


def invite_expires_at() -> datetime:
    """Return the expiry timestamp for a new invitation (now UTC + 7 days)."""
    return datetime.now(UTC) + timedelta(days=INVITE_TTL_DAYS)


def reset_expires_at() -> datetime:
    """Return the expiry timestamp for a new password-reset link (now UTC + 24 h)."""
    return datetime.now(UTC) + timedelta(hours=RESET_TTL_HOURS)
