"""Uniform error-code contract for Omniventory (M1.5 Step 1).

This module defines:
- ``AppError`` — the single exception type that services and routes raise.
- ``ErrorResponse`` — the wire schema documented in OpenAPI for every error path.
- ``ErrorCode`` — the stable registry of all error codes (FE↔BE contract).

Design decisions (roadmap §2.6, M1.5 §4.1):
- The backend emits **no** display text to end users; ``message`` is dev-facing
  English for logs/curl/unknown-code fallback.
- The envelope is **flat** (not nested under ``detail`` or ``error``) so the
  call site reads ``result.error.code`` with ``openapi-fetch``.
- Every error path — domain ``AppError``, stray ``HTTPException``, and Pydantic
  ``RequestValidationError`` — converges to this same shape via the exception
  handlers registered in ``app.main.create_app``.
"""

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Wire schema (appears in OpenAPI)
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Uniform error envelope returned by every error path.

    ``code``     Stable machine-readable key (e.g. ``location.not_found``).
                 The frontend maps this to a localized string.
    ``message``  Dev-facing English description.  Never shown verbatim to users.
    ``params``   Optional structured details (e.g. ``{"id": 42}``).
                 Machine-readable; the frontend uses them for string interpolation.
    """

    code: str
    message: str
    params: dict[str, object] | None = None


# ---------------------------------------------------------------------------
# Stable error-code registry (the FE↔BE contract)
# ---------------------------------------------------------------------------


class ErrorCode:
    """Stable error codes.  Renaming any constant requires a coordinated FE+BE change."""

    # --- Auth ---
    NOT_AUTHENTICATED = "auth.not_authenticated"
    SESSION_INVALID = "auth.session_invalid"
    ACCOUNT_INACTIVE = "auth.account_inactive"
    INVALID_CREDENTIALS = "auth.invalid_credentials"
    ACCOUNT_DISABLED = "auth.account_disabled"
    SETUP_ALREADY_COMPLETE = "auth.setup_already_complete"
    FORBIDDEN = "auth.forbidden"  # M6: insufficient role/permission

    # --- Validation ---
    INVALID_INPUT = "validation.invalid_input"
    UNSUPPORTED_LANGUAGE = "validation.unsupported_language"  # Step 2
    UNSUPPORTED_TRACKING_MODE = "validation.unsupported_tracking_mode"  # M2 Step 1
    UNSUPPORTED_STOCK_LEVEL = "validation.unsupported_stock_level"  # M2 Step 3

    # --- Tree (shared) ---
    TREE_CYCLE = "tree.cycle"
    TREE_DELETE_HAS_CHILDREN = "tree.delete_has_children"

    # --- Location ---
    LOCATION_NOT_FOUND = "location.not_found"
    LOCATION_PARENT_NOT_FOUND = "location.parent_not_found"
    LOCATION_DELETE_IN_USE = "location.delete_in_use"
    LOCATION_CONTAINER_LINK_CONFLICT = "location.container_link_conflict"

    # --- Category ---
    CATEGORY_NOT_FOUND = "category.not_found"
    CATEGORY_PARENT_NOT_FOUND = "category.parent_not_found"

    # --- Item kind ---
    ITEM_KIND_NOT_FOUND = "item_kind.not_found"

    # --- Item definition ---
    ITEM_DEFINITION_NOT_FOUND = "item_definition.not_found"
    ITEM_DEFINITION_HAS_INSTANCES = "item_definition.has_instances"
    ITEM_DEFINITION_TRACKING_MODE_CHANGE_CONFLICT = (
        "item_definition.tracking_mode_change_conflict"  # M2 Step 4
    )

    # --- Stock instance ---
    STOCK_INSTANCE_NOT_FOUND = "stock_instance.not_found"
    STOCK_INSTANCE_SERIAL_REQUIRES_QTY_ONE = "stock_instance.serial_requires_qty_one"
    STOCK_INSTANCE_SERIAL_DUPLICATE = "stock_instance.serial_duplicate"
    INSTANCE_FIELD_MODE_MISMATCH = "instance.field_mode_mismatch"  # M2 Step 3

    # --- Stock movements (M2 Step 4) ---
    STOCK_INSUFFICIENT = "stock.insufficient"
    STOCK_NEGATIVE_QUANTITY = "stock.negative_quantity"
    STOCK_MOVEMENT_NOT_APPLICABLE = "stock.movement_not_applicable"
    STOCK_MOVEMENT_NOT_FOUND = "stock.movement_not_found"
    STOCK_MOVEMENT_ALREADY_REVERSED = "stock.movement_already_reversed"
    STOCK_CANNOT_REVERSE_REVERSAL = "stock.cannot_reverse_reversal"
    STOCK_REVERSE_WOULD_GO_NEGATIVE = "stock.reverse_would_go_negative"

    # --- Notifications (M4 Step 6) ---
    NOTIFICATION_NOT_FOUND = "notification.not_found"

    # --- Integrations (M4 Step 8) ---
    INTEGRATION_INVALID_TOKEN = "integration.invalid_token"

    # --- Attachments (M5 Step 1) ---
    ATTACHMENT_NOT_FOUND = "attachment.not_found"
    ATTACHMENT_FILE_TOO_LARGE = "attachment.file_too_large"
    ATTACHMENT_UNSUPPORTED_TYPE = "attachment.unsupported_type"

    # --- Tags (M5 Step 2) ---
    TAG_NOT_FOUND = "tag.not_found"
    TAG_DUPLICATE_NAME = "tag.duplicate_name"

    # --- Notes (M5 Step 3) ---
    NOTE_NOT_FOUND = "note.not_found"

    # --- Barcodes (M5 Step 5) ---
    BARCODE_NOT_FOUND = "barcode.not_found"
    BARCODE_DUPLICATE = "barcode.duplicate"

    # --- Users (M6 Step 2) ---
    USER_NOT_FOUND = "user.not_found"
    USER_LAST_ADMIN = "user.last_admin"

    # --- Internal / catch-all ---
    INTERNAL_ERROR = "internal.error"


# Default dev-facing messages keyed by error code.
_DEFAULT_MESSAGES: dict[str, str] = {
    ErrorCode.NOT_AUTHENTICATED: "Authentication required.",
    ErrorCode.SESSION_INVALID: "Session expired or invalid.",
    ErrorCode.ACCOUNT_INACTIVE: "User account is inactive.",
    ErrorCode.INVALID_CREDENTIALS: "Invalid credentials.",
    ErrorCode.ACCOUNT_DISABLED: "Account is disabled.",
    ErrorCode.SETUP_ALREADY_COMPLETE: "Setup already complete.",
    ErrorCode.FORBIDDEN: "You do not have permission to perform this action.",
    ErrorCode.INVALID_INPUT: "Request validation failed.",
    ErrorCode.UNSUPPORTED_LANGUAGE: "Unsupported language code.",
    ErrorCode.UNSUPPORTED_TRACKING_MODE: "Unsupported stock tracking mode.",
    ErrorCode.UNSUPPORTED_STOCK_LEVEL: "Unsupported stock level value.",
    ErrorCode.TREE_CYCLE: "Operation would create a cycle in the tree.",
    ErrorCode.TREE_DELETE_HAS_CHILDREN: "Cannot delete a node that still has children.",
    ErrorCode.LOCATION_NOT_FOUND: "Location not found.",
    ErrorCode.LOCATION_PARENT_NOT_FOUND: "Parent location not found.",
    ErrorCode.LOCATION_DELETE_IN_USE: "Location cannot be deleted because it is in use.",
    ErrorCode.LOCATION_CONTAINER_LINK_CONFLICT: "Stock instance is already linked to another location.",
    ErrorCode.CATEGORY_NOT_FOUND: "Category not found.",
    ErrorCode.CATEGORY_PARENT_NOT_FOUND: "Parent category not found.",
    ErrorCode.ITEM_KIND_NOT_FOUND: "Item kind not found.",
    ErrorCode.ITEM_DEFINITION_NOT_FOUND: "Item definition not found.",
    ErrorCode.ITEM_DEFINITION_HAS_INSTANCES: "Item definition cannot be deleted because it still has instances.",
    ErrorCode.STOCK_INSTANCE_NOT_FOUND: "Stock instance not found.",
    ErrorCode.STOCK_INSTANCE_SERIAL_REQUIRES_QTY_ONE: "When a serial number is provided, quantity must be exactly 1.",
    ErrorCode.STOCK_INSTANCE_SERIAL_DUPLICATE: "Serial number is already registered for this definition.",
    ErrorCode.INSTANCE_FIELD_MODE_MISMATCH: "Field does not match the definition's stock tracking mode.",
    ErrorCode.ITEM_DEFINITION_TRACKING_MODE_CHANGE_CONFLICT: "Cannot change tracking mode when the definition already has stock instances.",
    ErrorCode.STOCK_INSUFFICIENT: "Insufficient stock to fulfil the requested quantity.",
    ErrorCode.STOCK_NEGATIVE_QUANTITY: "Operation would result in a negative quantity.",
    ErrorCode.STOCK_MOVEMENT_NOT_APPLICABLE: "Stock movements are not applicable to this definition's tracking mode.",
    ErrorCode.STOCK_MOVEMENT_NOT_FOUND: "Stock movement not found.",
    ErrorCode.STOCK_MOVEMENT_ALREADY_REVERSED: "This movement has already been reversed.",
    ErrorCode.STOCK_CANNOT_REVERSE_REVERSAL: "Cannot reverse a movement that is itself a reversal.",
    ErrorCode.STOCK_REVERSE_WOULD_GO_NEGATIVE: "Reversing this movement would drive the lot quantity below zero.",
    ErrorCode.NOTIFICATION_NOT_FOUND: "Notification not found or does not belong to the current user.",
    ErrorCode.INTEGRATION_INVALID_TOKEN: "Missing or invalid integration token.",
    ErrorCode.ATTACHMENT_NOT_FOUND: "Attachment not found.",
    ErrorCode.ATTACHMENT_FILE_TOO_LARGE: "Uploaded file exceeds the maximum allowed size.",
    ErrorCode.ATTACHMENT_UNSUPPORTED_TYPE: "File type is not supported.",
    ErrorCode.TAG_NOT_FOUND: "Tag not found.",
    ErrorCode.TAG_DUPLICATE_NAME: "A tag with that name already exists.",
    ErrorCode.NOTE_NOT_FOUND: "Note not found.",
    ErrorCode.BARCODE_NOT_FOUND: "Barcode not found.",
    ErrorCode.BARCODE_DUPLICATE: "A barcode with that code is already bound to a definition.",
    ErrorCode.USER_NOT_FOUND: "User not found.",
    ErrorCode.USER_LAST_ADMIN: "Cannot remove or demote the last active admin.",
    ErrorCode.INTERNAL_ERROR: "An internal error occurred.",
}


# ---------------------------------------------------------------------------
# Application exception
# ---------------------------------------------------------------------------


class AppError(Exception):
    """Domain exception that services and routes raise.

    The ``AppError`` exception handler in ``create_app`` converts this to an
    ``ErrorResponse`` HTTP response at ``status_code``.

    Parameters
    ----------
    code:
        Stable error code from ``ErrorCode``.
    status_code:
        HTTP status code (default 400).
    params:
        Optional structured details for the frontend (e.g. ``{"id": 42}``).
    message:
        Dev-facing English override.  When omitted the default for ``code``
        is used; when no default exists, ``code`` is used verbatim.
    """

    def __init__(
        self,
        code: str,
        *,
        status_code: int = 400,
        params: dict[str, object] | None = None,
        message: str | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.params = params
        self.message = message or _DEFAULT_MESSAGES.get(code, code)
        super().__init__(self.message)

    def to_response(self) -> ErrorResponse:
        """Convert to the wire ``ErrorResponse`` schema."""
        return ErrorResponse(code=self.code, message=self.message, params=self.params)
