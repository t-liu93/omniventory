"""Custom-fields schema utilities (M5 Step 4).

``CustomFieldsMap`` is an Annotated type alias that enforces:
- Each key must be a non-empty string ≤ ``KEY_MAX_LEN`` (64) characters.
- Each value must be a scalar: str, int, float, bool, or None.
  Nested dict/list values are rejected.
- String values are capped at ``STR_VALUE_MAX_LEN`` (1024) characters.
- Total field count capped at ``FIELD_COUNT_MAX`` (50).

Violations surface as ``validation.invalid_input`` (422) via the standard
Pydantic RequestValidationError handler — no new error codes needed.

Design note
-----------
The column stores a JSON object string in the DB (``Text``, NULL = none).
``serialize_custom_fields`` / ``deserialize_custom_fields`` are the sole
(de)serialization path; no DB JSON functions are used (roadmap §2.11).
Keys are sorted before storage for deterministic output, which also aids
the full-text substring search in M5 Step 6.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from pydantic import BeforeValidator

logger = logging.getLogger(__name__)

# Hard application limits — not runtime-configurable settings.
KEY_MAX_LEN: int = 64
STR_VALUE_MAX_LEN: int = 1024
FIELD_COUNT_MAX: int = 50

# Type alias for the plain Python representation.
ScalarValue = str | int | float | bool | None
CustomFieldsDict = dict[str, ScalarValue]


def _validate_custom_fields(v: Any) -> CustomFieldsDict:
    """Validate a custom-fields dict at the Pydantic schema boundary.

    Raises ``ValueError`` on any violation so Pydantic wraps it as a
    ``RequestValidationError`` (HTTP 422) via the existing error handler.

    Accepted input: a plain Python dict.  All other types are rejected.
    """
    if not isinstance(v, dict):
        raise ValueError(f"custom_fields must be a JSON object (dict); got {type(v).__name__!r}.")
    if len(v) > FIELD_COUNT_MAX:
        raise ValueError(
            f"custom_fields exceeds the maximum of {FIELD_COUNT_MAX} fields ({len(v)} provided)."
        )
    for key, value in v.items():
        # Key must be a non-empty string within the length cap.
        if not isinstance(key, str):
            raise ValueError(f"custom_fields keys must be strings; got {type(key).__name__!r}.")
        if not key:
            raise ValueError("custom_fields keys must not be empty strings.")
        if len(key) > KEY_MAX_LEN:
            raise ValueError(
                f"custom_fields key {key!r} exceeds the maximum key length of "
                f"{KEY_MAX_LEN} characters ({len(key)} characters)."
            )
        # Value must be a scalar — no nested dict or list (no nesting in M5).
        if isinstance(value, (dict, list)):
            raise ValueError(
                f"custom_fields value for key {key!r} must be a scalar "
                "(str, int, float, bool, or null); nested objects and arrays "
                "are not allowed."
            )
        # String values are length-capped.
        if isinstance(value, str) and len(value) > STR_VALUE_MAX_LEN:
            raise ValueError(
                f"custom_fields string value for key {key!r} exceeds the maximum "
                f"length of {STR_VALUE_MAX_LEN} characters ({len(value)} characters)."
            )
    result: CustomFieldsDict = dict(v)
    return result


# ---------------------------------------------------------------------------
# Annotated type — use as the field type on Create/Update schemas.
# ---------------------------------------------------------------------------

CustomFieldsMap = Annotated[CustomFieldsDict, BeforeValidator(_validate_custom_fields)]


# ---------------------------------------------------------------------------
# Storage helpers (used by services)
# ---------------------------------------------------------------------------


def serialize_custom_fields(fields: CustomFieldsDict | None) -> str | None:
    """Serialize a validated custom-fields dict to a JSON string for DB storage.

    Returns ``None`` when ``fields`` is ``None``.

    Storage format:
    - ``ensure_ascii=False`` — UTF-8-safe; non-ASCII chars stored verbatim.
    - Compact separators (``(",", ":")``).
    - ``sort_keys=True`` — deterministic output; aids substring search (Step 6).
    """
    if fields is None:
        return None
    return json.dumps(fields, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def deserialize_custom_fields(raw: str | None) -> CustomFieldsDict | None:
    """Parse a JSON string from the DB column back to a Python dict.

    Returns ``None`` for NULL / empty strings.  If the stored JSON is malformed
    (should not happen — we always write through ``serialize_custom_fields``),
    a warning is logged and ``None`` is returned defensively.
    """
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("custom_fields column contains invalid JSON — returning None.")
        return None
    if isinstance(parsed, dict):
        result: CustomFieldsDict = parsed
        return result
    logger.warning(
        "custom_fields column is not a JSON object (type=%s) — returning None.",
        type(parsed).__name__,
    )
    return None
