"""Add payment tracking to transactions

Revision ID: 070
Revises: 069
Create Date: 2026-08-15

Adds per-transaction payment status tracking:
- is_paid: Boolean flag (default False) indicating if transaction has been paid
- paid_date: DateTime when transaction was marked as paid (nullable)
- covered_by_payment_id: Self-referential FK to another transaction representing the payment that covered this transaction (nullable)

This allows users to track credit card transactions as paid/unpaid and optionally link
them to payment transactions.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "070"
down_revision = "069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add is_paid column with default False
    op.add_column(
        "transactions",
        sa.Column(
            "is_paid",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # Add paid_date column (nullable, set when marked as paid)
    op.add_column(
        "transactions",
        sa.Column(
            "paid_date",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Add covered_by_payment_id as self-referential FK
    op.add_column(
        "transactions",
        sa.Column(
            "covered_by_payment_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    # Create foreign key for self-referential payment relationship
    # ON DELETE SET NULL ensures if the covering payment is deleted, the reference is cleared
    op.create_foreign_key(
        "transactions_covered_by_payment_id_fkey",
        "transactions",
        "transactions",
        ["covered_by_payment_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Create indexes for query performance
    op.create_index(
        "ix_transactions_is_paid",
        "transactions",
        ["is_paid"],
    )

    op.create_index(
        "ix_transactions_bill_id_is_paid",
        "transactions",
        ["bill_id", "is_paid"],
    )

    op.create_index(
        "ix_transactions_covered_by_payment_id",
        "transactions",
        ["covered_by_payment_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_covered_by_payment_id", table_name="transactions")
    op.drop_index("ix_transactions_bill_id_is_paid", table_name="transactions")
    op.drop_index("ix_transactions_is_paid", table_name="transactions")
    op.drop_constraint(
        "transactions_covered_by_payment_id_fkey",
        "transactions",
        type_="foreignkey",
    )
    op.drop_column("transactions", "covered_by_payment_id")
    op.drop_column("transactions", "paid_date")
    op.drop_column("transactions", "is_paid")
