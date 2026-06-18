"""Alter stock_instances: ledger wiring, stock_level, received_at, backfill.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-18 00:00:00.000000 UTC

M2 Step 3 — wire ``stock_instances`` into the movement ledger.

Changes to ``stock_instances``:

1. **Add two new columns** (plain ``op.add_column`` — nullable / server-defaulted,
   no batch rebuild needed for these):
   - ``stock_level`` String(16) nullable — qualitative level for ``level``-mode lots.
   - ``received_at`` DateTime(tz) nullable, server_default=now() — FIFO key and
     physical-receipt timestamp (distinct from ``created_at``).

2. **Batch-alter** (SQLite cannot ALTER/re-CHECK in place):
   - ``quantity`` Numeric(18,6): change from NOT NULL → **nullable**.
   - Rewrite the serial CHECK from
       ``serial IS NULL OR quantity = 1``
     to
       ``serial IS NULL OR quantity IS NULL OR quantity = 1``
     This allows NULL quantity for ``level``/``none`` lots while still blocking
     a non-NULL quantity != 1 when a serial is set.
   - The partial unique index ``(definition_id, serial) WHERE serial IS NOT NULL``
     is unchanged and must be reproduced in the batch rebuild.

3. **Backfill** (after all column changes are in place):
   - Set ``received_at = created_at`` on every pre-existing row (all were created
     in M1 as ``exact`` lots, so ``received_at`` is meaningful for FIFO).
   - Insert one ``intake`` movement per pre-existing lot with:
       ``instance_id = lot.id``
       ``type = 'intake'``
       ``quantity_delta = lot.quantity``   (the original pre-existing quantity)
       ``occurred_at = lot.created_at``    (backdated to when the lot was created)
       ``to_location_id = lot.location_id``  (intake provenance)
       ``user_id = NULL``                  (system / backfill actor, per M2 §2
                                            "Movement actor": backfill sets user_id = NULL)
   This realises M2 §3.3 — quantity becomes ledger-derived without changing any
   displayed number.

Downgrade:
   - Delete the backfilled system ``intake`` movements.  These are identified by
     ``type = 'intake' AND user_id IS NULL AND reverses_movement_id IS NULL``.
     Per M2 §2 "Movement actor", backfill is the **only** source of user_id = NULL
     movements; operational/create-time intakes always carry the acting user from
     RequestContext.  Using ``user_id IS NULL`` as the distinguishing predicate is
     therefore correct and stable — it will not accidentally delete operational intakes
     inserted by later steps (Steps 4+) because those always have a real user_id.
   - Drop ``level``/``none`` lots (``quantity IS NULL``) before reverting the NOT NULL
     constraint.  These rows were created under the 0012 feature and cannot be
     represented in the pre-0012 schema (no ``stock_level`` column, ``quantity NOT NULL``).
     They carry no ledger movements (level/none have no ledger), so no orphan-movement
     cleanup is needed; the downgrade docstring documents that these rows are lost.
   - Batch-revert ``quantity`` back to NOT NULL and restore the original serial CHECK.
   - Drop ``stock_level`` and ``received_at``.
   Fully reversible for ``exact`` lots.  ``level``/``none`` lots created after the
   upgrade are **discarded** on downgrade (they cannot be expressed in the 0011 schema).
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Add stock_level and received_at; batch-alter quantity→nullable + rewrite CHECK;
    backfill one intake movement per pre-existing lot."""

    # ── Step 1: Add the two new nullable columns ──────────────────────────────
    # These are nullable / server-defaulted columns so SQLite can add them
    # without a full table rebuild (no batch mode required here).
    op.add_column(
        "stock_instances",
        sa.Column("stock_level", sa.String(16), nullable=True),
    )
    op.add_column(
        "stock_instances",
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=True,
            # No server_default here: SQLite forbids non-constant defaults on
            # ADD COLUMN.  The backfill step (3a) sets received_at = created_at
            # for all pre-existing rows; the batch rebuild below adds a
            # server_default for future inserts.
        ),
    )

    # ── Step 2: Batch-alter to change quantity → nullable and rewrite CHECK ───
    # The batch rebuild drops and recreates the table under a temporary name,
    # carrying over all constraints and indexes that we list explicitly.
    # We also use this batch pass to add the server_default to received_at
    # (SQLite allows server_default on a column inside a batch rebuild).
    with op.batch_alter_table("stock_instances", schema=None) as batch_op:
        # Alter quantity column: NOT NULL → nullable (keep Numeric(18,6) type).
        batch_op.alter_column(
            "quantity",
            existing_type=sa.Numeric(18, 6),
            nullable=True,
            server_default=None,  # remove the server_default="1" from M1
        )
        # Add server_default to received_at now that we're inside a batch rebuild.
        batch_op.alter_column(
            "received_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        )
        # Drop the old CHECK constraint (the batch rebuild will create the new one).
        batch_op.drop_constraint("ck_stock_instances_serial_qty_1", type_="check")
        # Create the new CHECK: allows NULL quantity for level/none lots.
        batch_op.create_check_constraint(
            "ck_stock_instances_serial_qty_1",
            "serial IS NULL OR quantity IS NULL OR quantity = 1",
        )

    # ── Step 3: Backfill ─────────────────────────────────────────────────────
    # Use raw SQL via op.get_bind() so we can run DML against the already-
    # altered table without going through the ORM (which would need the model
    # to match the new schema — fine, but raw SQL is simpler here).
    conn = op.get_bind()

    # 3a. Set received_at = created_at on all pre-existing lots.
    conn.execute(
        sa.text("UPDATE stock_instances SET received_at = created_at WHERE received_at IS NULL")
    )

    # 3b. Insert one intake movement per lot.
    # We read all lots that have a non-NULL quantity (which is all of them at
    # this point — no lot yet has NULL quantity after a fresh migration from M1).
    rows = conn.execute(
        sa.text("SELECT id, quantity, location_id, created_at FROM stock_instances")
    ).fetchall()

    for row in rows:
        inst_id, qty, location_id, created_at = row
        if qty is None:
            continue  # defensive — should not happen on a clean M1 DB
        conn.execute(
            sa.text(
                """
                INSERT INTO stock_movements
                    (instance_id, type, quantity_delta, to_location_id,
                     occurred_at, user_id, created_at)
                VALUES
                    (:instance_id, 'intake', :qty, :to_loc,
                     :occurred_at, NULL, :created_at_val)
                """
            ),
            {
                "instance_id": inst_id,
                "qty": qty,
                "to_loc": location_id,
                "occurred_at": created_at,
                "created_at_val": created_at,
            },
        )


def downgrade() -> None:
    """Delete backfilled movements; discard level/none lots; batch-revert quantity NOT NULL
    + old CHECK; drop stock_level and received_at."""

    conn = op.get_bind()

    # ── Step 1: Delete the backfilled system intake movements ─────────────────
    # Backfill movements are distinguished by user_id IS NULL (M2 §2 "Movement
    # actor": the backfill is the only source of NULL-user movements; all
    # operational/create-time intakes carry a real user_id from RequestContext).
    # We additionally require type = 'intake' AND reverses_movement_id IS NULL
    # for belt-and-suspenders, but user_id IS NULL is the load-bearing filter
    # that prevents this predicate from ever touching operational intakes in
    # Steps 4+.
    conn.execute(
        sa.text(
            "DELETE FROM stock_movements "
            "WHERE type = 'intake' AND user_id IS NULL AND reverses_movement_id IS NULL"
        )
    )

    # ── Step 1b: Delete level/none lots (quantity IS NULL) ────────────────────
    # These rows were created under the 0012 feature (level/none tracking modes)
    # and cannot be expressed in the pre-0012 schema: 0011 has quantity NOT NULL
    # and no stock_level column.  They carry no ledger movements, so deleting
    # them does not leave orphan movements.  The downgrade docstring above
    # documents that these rows are lost on downgrade.
    conn.execute(sa.text("DELETE FROM stock_instances WHERE quantity IS NULL"))

    # ── Step 2: Batch-revert quantity to NOT NULL + restore original CHECK ────
    with op.batch_alter_table("stock_instances", schema=None) as batch_op:
        # Drop the new CHECK.
        batch_op.drop_constraint("ck_stock_instances_serial_qty_1", type_="check")
        # Restore the original CHECK.
        batch_op.create_check_constraint(
            "ck_stock_instances_serial_qty_1",
            "serial IS NULL OR quantity = 1",
        )
        # Revert quantity to NOT NULL.
        batch_op.alter_column(
            "quantity",
            existing_type=sa.Numeric(18, 6),
            nullable=False,
            server_default="1",
        )

    # ── Step 3: Drop the two new columns ─────────────────────────────────────
    # SQLite cannot DROP COLUMN directly in some versions; batch mode handles it.
    with op.batch_alter_table("stock_instances", schema=None) as batch_op:
        batch_op.drop_column("received_at")
        batch_op.drop_column("stock_level")
