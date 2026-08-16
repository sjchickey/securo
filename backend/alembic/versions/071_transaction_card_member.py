"""Add cardholder to transactions

Revision ID: 071
Revises: 070
Create Date: 2026-08-16

Adds `card_member`: which cardholder made the transaction, for cards with
supplementary holders. Amex exports carry it — in CSV as a "Card Member"
column, and in OFX/QFX inside the MEMO as "MR SEAN HICKEY-41006".

Plain text rather than a table: an account has two or three of these and the
issuer writes the name consistently, so a lookup table would add management
UI for no gain. Indexed because the transactions list filters on it.
"""

from alembic import op
import sqlalchemy as sa

revision = "071"
down_revision = "070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("card_member", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_transactions_card_member",
        "transactions",
        ["card_member"],
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_card_member", table_name="transactions")
    op.drop_column("transactions", "card_member")
