"""Polymorphic owner registry for M5 cross-cutting capabilities.

``OWNER_TYPES`` is the single registry of allowed ``model_type`` values.  It is
used for:
- Schema / service-layer validation of ``model_type`` strings.
- Owner-existence checks (``resolve_owner``).
- Cascade maps used by the three entity delete services.

Adding a future owner type (e.g. ``category``) is a one-line registry change.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

OWNER_TYPES: frozenset[str] = frozenset({"item_definition", "stock_instance", "location"})


# ---------------------------------------------------------------------------
# Owner resolution
# ---------------------------------------------------------------------------


def resolve_owner(db: Session, model_type: str, model_id: int) -> object:
    """Resolve and return the owner ORM object, or raise an appropriate error.

    Parameters
    ----------
    db:
        The active SQLAlchemy session.
    model_type:
        One of the values in ``OWNER_TYPES``.  Bad type → ``validation.invalid_input``.
    model_id:
        The owner's PK.  Missing owner → the owner's not-found error code.

    Returns
    -------
    The ORM object (ItemDefinition, StockInstance, or Location).

    Raises
    ------
    AppError(validation.invalid_input, 422)
        When ``model_type`` is not in ``OWNER_TYPES``.
    AppError(<owner>.not_found, 404)
        When the owner row does not exist.
    """
    if model_type not in OWNER_TYPES:
        raise AppError(
            ErrorCode.INVALID_INPUT,
            status_code=422,
            params={"model_type": model_type, "allowed": sorted(OWNER_TYPES)},
            message=(f"Invalid model_type {model_type!r}. Allowed values: {sorted(OWNER_TYPES)}."),
        )

    if model_type == "item_definition":
        from app.models.item_definition import ItemDefinition

        defn = db.get(ItemDefinition, model_id)
        if defn is None:
            raise AppError(
                ErrorCode.ITEM_DEFINITION_NOT_FOUND,
                status_code=404,
                params={"id": model_id},
                message=f"Item definition {model_id} not found.",
            )
        return defn

    if model_type == "stock_instance":
        from app.models.stock_instance import StockInstance

        inst = db.get(StockInstance, model_id)
        if inst is None:
            raise AppError(
                ErrorCode.STOCK_INSTANCE_NOT_FOUND,
                status_code=404,
                params={"id": model_id},
                message=f"Stock instance {model_id} not found.",
            )
        return inst

    # model_type == "location"
    from app.models.location import Location

    loc = db.get(Location, model_id)
    if loc is None:
        raise AppError(
            ErrorCode.LOCATION_NOT_FOUND,
            status_code=404,
            params={"id": model_id},
            message=f"Location {model_id} not found.",
        )
    return loc
