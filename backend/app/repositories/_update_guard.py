"""Shared repository-layer guard for PATCH null-on-non-nullable columns.

This module provides :func:`reject_null_on_non_nullable`, a defensive helper
that converts a silent SQLAlchemy/DB ``IntegrityError`` (500) into a clean
422 ``validation.invalid_input`` ``AppError`` when a PATCH body passes an
explicit ``null`` for a column that is ``nullable=False`` in the schema.

Usage
-----
Call at the **top** of any repository ``update()`` method that applies
client-supplied fields via a blind ``setattr`` loop::

    from app.repositories._update_guard import reject_null_on_non_nullable

    def update(self, instance: MyModel, **fields: object) -> MyModel:
        reject_null_on_non_nullable(instance, fields)
        for key, value in fields.items():
            setattr(instance, key, value)
        self._db.flush()
        return instance

Design notes
------------
- Uses ``sqlalchemy.orm.class_mapper(type(instance))`` to get the ORM mapper
  at call time.  The mapper is cached by SQLAlchemy so inspection is cheap.
- Resolves each key via ``mapper.column_attrs`` (attribute name → mapped
  column), which is correct even when the Python attribute name differs from
  the underlying column name.
- Skips keys that are not mapped column attributes (e.g. relationships) —
  defensive against non-column kwargs.
- Only fires for ``None`` values; non-None values and nullable columns are
  passed through as a no-op.
- Primary-key columns are skipped (they are never updated via PATCH).
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.orm import class_mapper

from app.core.errors import AppError, ErrorCode


def reject_null_on_non_nullable(instance: object, fields: Mapping[str, object]) -> None:
    """Raise ``validation.invalid_input`` (422) for None on a NOT NULL column.

    Iterates over ``fields`` and checks each key against the SQLAlchemy mapper
    of ``instance``.  If a key maps to a non-nullable, non-PK column **and**
    the supplied value is ``None``, raises an :class:`~app.core.errors.AppError`
    with code ``validation.invalid_input`` (HTTP 422).

    A no-op when:
    - The value is not ``None``.
    - The column is nullable (``nullable=True``).
    - The key does not correspond to a mapped column attribute (e.g. a
      relationship name or unknown kwarg) — defensive.
    - The column is a primary key (PKs are never updated via PATCH).

    Parameters
    ----------
    instance:
        An ORM model instance.  Its class must be a mapped SQLAlchemy model.
    fields:
        A mapping of attribute-name → proposed value, as collected from the
        client PATCH body (``model_fields_set``).

    Raises
    ------
    AppError(code=validation.invalid_input, status_code=422)
        When any key in ``fields`` maps to a NOT NULL non-PK column and the
        value is ``None``.
    """
    mapper = class_mapper(type(instance))
    column_attrs = mapper.column_attrs  # attribute-name → ColumnProperty

    for key, value in fields.items():
        if value is not None:
            # Non-None values are always fine at this layer.
            continue

        if key not in column_attrs:
            # Not a mapped column attribute (relationship, unknown kwarg) — skip.
            continue

        col_prop = column_attrs[key]
        # column_attrs[key] is a ColumnProperty; its columns list holds the
        # actual Column objects.  A ColumnProperty typically has exactly one
        # column.
        for col in col_prop.columns:
            if col.primary_key:
                # PKs are never updated via PATCH — skip.
                continue
            if not col.nullable:
                raise AppError(
                    ErrorCode.INVALID_INPUT,
                    status_code=422,
                    params={"field": key},
                    message=f"Field '{key}' cannot be null.",
                )
