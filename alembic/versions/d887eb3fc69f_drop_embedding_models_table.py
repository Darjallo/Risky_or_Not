"""drop embedding_models table

Revision ID: d887eb3fc69f
Revises: c86f5e0c64f8
Create Date: 2026-01-07 17:56:05.677561
"""
from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision = "d887eb3fc69f"
down_revision = "c86f5e0c64f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("embedding_models")


def downgrade() -> None:
    op.create_table(
        "embedding_models",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("table_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

